"""Bash command write-target extraction (stdlib-only, IL-0).

Independent re-implementation of the write-target extraction logic from
hooks/_command_classification.py and hooks/guards/write_guard.py, suitable
for import by IL-1 modules (execution/).

hooks/ retains its own function bodies unchanged because hook scripts import
via sys.path manipulation and cannot use autoskillit package imports.
A parity test corpus guards against drift between the two implementations.
"""

from __future__ import annotations

import os
import re
import shlex

_SHELL_OPS: frozenset[str] = frozenset({"&&", "||", ";", "!", "|", "("})

_REDIRECT_TOKEN_RE = re.compile(r"^(\d*)>{1,2}(.+)$")
_REDIRECT_OP_ONLY_RE = re.compile(r"^(\d*)>{1,2}$")
_FD_REDIRECT_RE = re.compile(r"^\d*>{1,2}&")
_TRAILING_SHELL_CLOSERS = frozenset({")", "`", "}", "'", '"', ";", "&", "|"})

_PSEUDO_DEVICE_PATHS: frozenset[str] = frozenset(
    {
        "/dev/null",
        "/dev/zero",
        "/dev/stdout",
        "/dev/stderr",
        "/dev/stdin",
    }
)

_WRITE_VERBS: frozenset[str] = frozenset(
    {
        "sed",
        "tee",
        "mv",
        "cp",
        "patch",
        "rm",
        "unlink",
    }
)

_GIT_FLAG_WITH_VALUE: frozenset[str] = frozenset({"-C", "--git-dir", "--work-tree", "-c"})


def _resolve_write_target(path: str, cwd: str = "") -> str | None:
    if not path:
        return None
    if path.startswith("&") or _FD_REDIRECT_RE.match(path):
        return None
    if os.path.isabs(path):
        return path
    if cwd:
        return os.path.join(cwd, path)
    return None


def _tokenize_command_segments(command: str) -> list[list[str]]:
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


def _extract_redirect_targets(tokens: list[str], cwd: str = "") -> list[str]:
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
                resolved = _resolve_write_target(path, cwd)
                if resolved is not None:
                    targets.append(resolved)
                i += 2
                continue
        elif _REDIRECT_OP_ONLY_RE.match(tok):
            if i + 1 < len(tokens):
                path = tokens[i + 1]
                while path and path[-1] in _TRAILING_SHELL_CLOSERS:
                    path = path[:-1]
                resolved = _resolve_write_target(path, cwd)
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
                resolved = _resolve_write_target(path, cwd)
                if resolved is not None:
                    targets.append(resolved)
        i += 1
    return targets


def _command_verb(segment: list[str]) -> str:
    if not segment:
        return ""
    start = 0
    if segment[0] == "env" and len(segment) > 1:
        start = 1
        while start < len(segment) and (segment[start].startswith("-") or "=" in segment[start]):
            start += 1
    return segment[start] if start < len(segment) else ""


def _is_gh_command(segment: list[str]) -> bool:
    return _command_verb(segment) == "gh"


def _extract_segment_targets(segment: list[str], cwd: str) -> list[str] | None:
    if _is_gh_command(segment):
        return None

    verb = _command_verb(segment)
    targets: list[str] = []
    found_write = False

    if verb == "git" and len(segment) >= 2:
        idx = 1
        while idx < len(segment):
            tok = segment[idx]
            if tok in _GIT_FLAG_WITH_VALUE:
                idx += 2
                if idx >= len(segment):
                    break
            elif tok.startswith("-") and "=" not in tok and tok not in ("--", "--hard"):
                idx += 1
            else:
                break
        if idx < len(segment):
            subcmd = segment[idx]
            if subcmd == "checkout" and "--" in segment[idx + 1 :]:
                found_write = True
                double_dash = segment.index("--", idx + 1)
                for t in segment[double_dash + 1 :]:
                    resolved = _resolve_write_target(t, cwd)
                    if resolved is not None and resolved not in _PSEUDO_DEVICE_PATHS:
                        targets.append(resolved)
            elif subcmd == "reset" and "--hard" in segment[idx + 1 :]:
                found_write = True
    elif verb in _WRITE_VERBS:
        found_write = True
        non_flag = [
            t
            for t in segment[1:]
            if not t.startswith("-") and not t.startswith("&") and not _FD_REDIRECT_RE.match(t)
        ]
        if verb == "sed":
            flags = [t for t in segment[1:] if t.startswith("-")]
            has_inplace = any(t.startswith("-i") or t == "--in-place" for t in flags)
            if has_inplace and non_flag:
                path = non_flag[-1]
                resolved = _resolve_write_target(path, cwd)
                if resolved is not None and resolved not in _PSEUDO_DEVICE_PATHS:
                    targets.append(resolved)
        elif verb == "tee":
            if non_flag:
                path = non_flag[0]
                resolved = _resolve_write_target(path, cwd)
                if resolved is not None and resolved not in _PSEUDO_DEVICE_PATHS:
                    targets.append(resolved)
        elif verb in ("mv", "cp"):
            if len(non_flag) >= 2:
                path = non_flag[-1]
                resolved = _resolve_write_target(path, cwd)
                if resolved is not None and resolved not in _PSEUDO_DEVICE_PATHS:
                    targets.append(resolved)
        elif verb == "patch":
            for t in non_flag:
                resolved = _resolve_write_target(t, cwd)
                if resolved is not None:
                    if resolved not in _PSEUDO_DEVICE_PATHS:
                        targets.append(resolved)
                    break
        elif verb in ("rm", "unlink"):
            for t in non_flag:
                resolved = _resolve_write_target(t, cwd)
                if resolved is not None and resolved not in _PSEUDO_DEVICE_PATHS:
                    targets.append(resolved)

    if found_write:
        return targets
    return None


def extract_bash_write_targets(command: str, cwd: str = "") -> list[str]:
    """Extract filesystem write targets from a Bash command string.

    Uses shlex tokenization + verb-aware segment dispatch + redirect
    extraction. Returns only paths that are actual write destinations
    (redirect targets, tee targets, cp/mv destinations, sed -i targets).

    Returns [] for read-only commands, slash-command tokens, and URL paths.
    Pseudo-device paths (/dev/null, /dev/stderr, etc.) are excluded.
    """
    segments = _tokenize_command_segments(command)
    if cwd and not os.path.isabs(cwd):
        cwd = ""

    all_targets: list[str] = []

    for segment in segments:
        result = _extract_segment_targets(segment, cwd)
        if result is not None:
            all_targets.extend(result)

    try:
        flat_tokens = shlex.split(command)
    except (ValueError, TypeError, AttributeError):
        flat_tokens = []
    redirect_paths = _extract_redirect_targets(flat_tokens, cwd)
    for path in redirect_paths:
        if path not in _PSEUDO_DEVICE_PATHS:
            all_targets.append(path)

    seen: set[str] = set()
    unique: list[str] = []
    for t in all_targets:
        if t not in seen:
            seen.add(t)
            unique.append(t)
    return unique
