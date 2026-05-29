"""Shared command classification primitives for guard scripts.

Supported interpreters: python3?, perl, ruby, node.
To add coverage for a new interpreter, update _INTERPRETER_RE only.
"""

from __future__ import annotations

import re
import shlex
from collections.abc import Sequence

_INTERPRETER_RE = re.compile(
    r"(?:^|&&|\|\||;)\s*(?:env\s+)?(?:python3?|perl|ruby|node)\s+"
    r"(?:-[ce]\s|.*<<)"
)

_NESTED_SHELL_RE = re.compile(r"(?:^|&&|\|\||;)\s*(?:bash|sh|zsh|dash)\s+-c\s+")

_WRITE_APIS_RE = re.compile(
    r"\.write_text\s*\(|\.write_bytes\s*\("
    r"|open\s*\([^)]*['\"][wWaA]\+?[bB]?['\"]"
    r"|shutil\.(?:copy|move|copyfile|copytree)\s*\("
)

_SUBPROCESS_APIS_RE = re.compile(
    r"subprocess\.(?:run|call|Popen|check_call|check_output)\s*\("
    r"|os\.(?:system|popen|exec[lv]p?e?)\s*\("
)

_LITERAL_OPEN_PATH_RE = re.compile(r"""open\s*\(\s*(['"])(/[^'"]+)\1\s*,\s*['"][wWaA]""")
_LITERAL_PATH_CONSTRUCTOR_RE = re.compile(
    r"""Path\s*\(\s*(['"])(/[^'"]+)\1\s*\)\s*\.(?:write_text|write_bytes)\s*\("""
)

_SHELL_OPS: frozenset[str] = frozenset({"&&", "||", ";", "!", "|", "("})


def tokenize_command_segments(command: str) -> list[list[str]]:
    """Split a shell command into segments of (verb, args...) token lists.

    Each segment is one logical command, separated by shell operators.
    Returns [] on shlex parse error (unclosed quotes).
    """
    try:
        tokens = shlex.split(command)
    except (ValueError, TypeError):
        return []
    segments: list[list[str]] = []
    current: list[str] = []
    for token in tokens:
        if token in _SHELL_OPS:
            if current:
                segments.append(current)
                current = []
        else:
            current.append(token)
    if current:
        segments.append(current)
    return segments


def command_verb(segment: list[str]) -> str:
    """Return the command verb from a segment, skipping 'env' prefix."""
    if not segment:
        return ""
    start = 0
    if segment[0] == "env" and len(segment) > 1:
        start = 1
        while start < len(segment) and (segment[start].startswith("-") or "=" in segment[start]):
            start += 1
    return segment[start] if start < len(segment) else ""


def is_gh_command(segment: list[str]) -> bool:
    """Return True if the segment's command verb is 'gh'."""
    return command_verb(segment) == "gh"


def has_interpreter_write(command: str) -> bool:
    if not _INTERPRETER_RE.search(command):
        return False
    return bool(_WRITE_APIS_RE.search(command))


def extract_interpreter_write_path(command: str) -> str | None:
    """Extract the literal file path from an interpreter write command.

    Returns the path string when a static literal is found.
    Returns None when:
    - The command is not an interpreter write (no interpreter prefix or no write API)
    - The path is constructed dynamically (variable, f-string, concatenation)
    - The write API is shutil.copy/move (two paths — ambiguous which is the target)

    shutil.* calls intentionally return None (fall through to has_interpreter_write's
    unconditional deny) because they have two path arguments and extracting the correct
    target requires deeper parsing than regex can reliably provide.
    """
    if not _INTERPRETER_RE.search(command):
        return None
    if not _WRITE_APIS_RE.search(command):
        return None
    m = _LITERAL_OPEN_PATH_RE.search(command)
    if m:
        return m.group(2)
    m = _LITERAL_PATH_CONSTRUCTOR_RE.search(command)
    if m:
        return m.group(2)
    return None


def has_interpreter_wrapped_command(command: str, *, target_commands: Sequence[str]) -> bool:
    if not _INTERPRETER_RE.search(command):
        return False
    if not _SUBPROCESS_APIS_RE.search(command):
        return False
    cmd_lower = command.lower()
    return any(tc.lower() in cmd_lower for tc in target_commands)


def has_nested_shell(command: str) -> bool:
    return bool(_NESTED_SHELL_RE.search(command))
