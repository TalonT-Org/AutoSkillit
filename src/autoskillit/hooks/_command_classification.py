"""Shared command classification primitives for guard scripts.

Supported interpreters: python3?, perl, ruby, node.
To add coverage for a new interpreter, update both _INTERPRETER_RE and
_INTERPRETER_LINE_RE.
"""

from __future__ import annotations

import os
import re
import shlex
from collections.abc import Sequence

_INTERPRETER_RE = re.compile(
    r"(?:^|&&|\|\||;)\s*(?:env\s+)?(?:python3?|perl|ruby|node)\s+"
    r"(?:-[ce]\s|.*<<)"
)

_INTERPRETER_LINE_RE = re.compile(r"(?:python3?|perl|ruby|node)\s+(?:-[ce]\s|.*<<)")

_NESTED_SHELL_RE = re.compile(r"(?:^|&&|\|\||;)\s*(?:bash|sh|zsh|dash)\s+-c\s+")

_WRITE_APIS_RE = re.compile(
    r"\.write_text\s*\(|\.write_bytes\s*\("
    r"|open\s*\([^)]*['\"][wWaAxX]\+?[bB]?['\"]"
    r"|shutil\.(?:copy|move|copyfile|copytree)\s*\("
)

_SUBPROCESS_APIS_RE = re.compile(
    r"subprocess\.(?:run|call|Popen|check_call|check_output)\s*\("
    r"|os\.(?:system|popen|exec[lv]p?e?)\s*\("
)

_LITERAL_OPEN_PATH_RE = re.compile(r"""open\s*\(\s*(['"])([^'"]+)\1\s*,\s*['"][wWaAxX]""")
_LITERAL_PATH_CONSTRUCTOR_RE = re.compile(
    r"""Path\s*\(\s*(['"])([^'"]+)\1\s*\)\s*\.(?:write_text|write_bytes)\s*\("""
)

_WRITE_CALL_SITE_RE = re.compile(
    r"open\s*\([^)]*['\"][wWaAxX]\+?[bB]?['\"]"
    r"|Path\s*\([^)]*\)\s*\.(?:write_text|write_bytes)\s*\("
    r"|shutil\.(?:copy|move|copyfile|copytree)\s*\("
)

_SHELL_OPS: frozenset[str] = frozenset({"&&", "||", ";", "!", "|", "("})

_REDIRECT_TOKEN_RE = re.compile(r"^(\d*)>{1,2}(.+)$")
_REDIRECT_OP_ONLY_RE = re.compile(r"^(\d*)>{1,2}$")
_FD_REDIRECT_RE = re.compile(r"^\d*>{1,2}&")
_TRAILING_SHELL_CLOSERS = frozenset({")", "`", "}", "'", '"', ";", "&", "|"})
_SHELL_VAR_RE = re.compile(r"\$\{[A-Za-z_]|\$[A-Za-z_]")


def resolve_write_target(path: str, cwd: str = "") -> str | None:
    if not path:
        return None
    if path.startswith("&") or _FD_REDIRECT_RE.match(path):
        return None
    if _SHELL_VAR_RE.search(path):
        path = os.path.expandvars(path)
        if _SHELL_VAR_RE.search(path):
            return None
    if os.path.isabs(path):
        return path
    if cwd:
        return os.path.join(cwd, path)
    return None


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


def extract_redirect_targets(tokens: list[str], cwd: str = "") -> list[str]:
    """Extract redirect target paths from shlex-tokenized command tokens.

    Returns resolved paths including pseudo-devices — caller filters.
    Relative paths are resolved against cwd when provided.

    Handles three redirect forms at depth 0 only:
    - Separate:  ['>', '/path'] or ['>>', '/path']
    - Split:     ['2>', '/path'] (operator-only token + next token)
    - Merged:    ['2>/path'] or ['2>>/path']

    Tracks subshell nesting via '(' and ')' — both standalone and fused
    with adjacent text (shlex merges '(' with following chars in POSIX mode).
    """
    targets: list[str] = []
    depth = 0
    i = 0
    while i < len(tokens):
        tok = tokens[i]
        if tok == "(" or (tok.startswith("(") and len(tok) > 1):
            depth += 1
            if tok.endswith(")") and len(tok) > 1:
                depth -= 1
            i += 1
            continue
        if tok == ")":
            if depth > 0:
                depth -= 1
            i += 1
            continue
        if tok.endswith(")") and len(tok) > 1:
            if depth > 0:
                depth -= 1
            i += 1
            continue
        if depth > 0:
            i += 1
            continue
        if tok in (">", ">>"):
            if i + 1 < len(tokens):
                path = tokens[i + 1]
                while path and path[-1] in _TRAILING_SHELL_CLOSERS:
                    path = path[:-1]
                resolved = resolve_write_target(path, cwd)
                if resolved is not None:
                    targets.append(resolved)
                i += 2
                continue
        elif _REDIRECT_OP_ONLY_RE.match(tok):
            if i + 1 < len(tokens):
                path = tokens[i + 1]
                while path and path[-1] in _TRAILING_SHELL_CLOSERS:
                    path = path[:-1]
                resolved = resolve_write_target(path, cwd)
                if resolved is not None:
                    targets.append(resolved)
                i += 2
                continue
        else:
            m = _REDIRECT_TOKEN_RE.match(tok)
            if m:
                path = m.group(2)
                while path and path[-1] in _TRAILING_SHELL_CLOSERS:
                    path = path[:-1]
                resolved = resolve_write_target(path, cwd)
                if resolved is not None:
                    targets.append(resolved)
        i += 1
    return targets


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


def is_git_command(segment: list[str]) -> bool:
    """Return True if the segment's command verb is 'git' or ends with '/git'."""
    verb = command_verb(segment)
    return verb == "git" or verb.endswith("/git")


_GIT_GLOBAL_FLAGS: frozenset[str] = frozenset(
    {"-C", "--work-tree", "--git-dir", "--no-pager", "--bare", "-c"}
)
_GIT_GLOBAL_FLAGS_WITH_VALUE: frozenset[str] = frozenset({"-C", "--work-tree", "--git-dir", "-c"})


def extract_git_subcommand_and_flags(
    segment: list[str],
) -> tuple[str, list[str]] | None:
    """Extract the git subcommand and its flags from a tokenized segment.

    Skips global git flags (and their value tokens) to find the subcommand,
    then returns (subcommand, remaining_tokens). Returns None if the segment
    is not a git command or has no subcommand.
    """
    if not segment:
        return None
    verb = segment[0]
    if verb != "git" and not verb.endswith("/git"):
        return None
    i = 1
    while i < len(segment):
        token = segment[i]
        if token in _GIT_GLOBAL_FLAGS_WITH_VALUE:
            i += 2  # skip flag and its value
            continue
        if token in _GIT_GLOBAL_FLAGS:
            i += 1
            continue
        if token.startswith("-"):
            i += 1
            continue
        # First non-flag token is the subcommand
        subcommand = token
        remaining = segment[i + 1 :]
        return (subcommand, remaining)
    return None


def has_interpreter_write(command: str) -> bool:
    if not _INTERPRETER_RE.search(command):
        return False
    return bool(_WRITE_APIS_RE.search(command))


def extract_interpreter_write_paths(command: str) -> list[str] | None:
    """Extract literal file paths from an interpreter write command.

    Returns:
        None    — command is not an interpreter write (no prefix or no write API).
        []      — interpreter write detected but not all paths are static literals
                  (dynamic variable, f-string, shutil two-arg, or mixed).
        [paths] — all write target paths are static literals (may be relative).
    """
    if not _INTERPRETER_RE.search(command):
        return None
    if not _WRITE_APIS_RE.search(command):
        return None

    call_site_count = len(_WRITE_CALL_SITE_RE.findall(command))

    paths: list[str] = []
    for m in _LITERAL_OPEN_PATH_RE.finditer(command):
        paths.append(m.group(2))
    for m in _LITERAL_PATH_CONSTRUCTOR_RE.finditer(command):
        paths.append(m.group(2))

    if len(paths) < call_site_count:
        return []

    return paths if paths else []


def has_interpreter_wrapped_command(command: str, *, target_commands: Sequence[str]) -> bool:
    if not _INTERPRETER_RE.search(command):
        return False
    if not _SUBPROCESS_APIS_RE.search(command):
        return False
    cmd_lower = command.lower()
    return any(tc.lower() in cmd_lower for tc in target_commands)


def has_nested_shell(command: str) -> bool:
    return bool(_NESTED_SHELL_RE.search(command))
