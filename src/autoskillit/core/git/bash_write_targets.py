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
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

__all__ = ["contains_test_gate_command", "extract_bash_write_targets"]

_SPLIT_TOKENS: frozenset[str] = frozenset({"&&", "||", ";", "|", "&"})
_CANONICAL_TEST_GATE_COMMANDS: frozenset[tuple[str, ...]] = frozenset(
    {
        ("task", "test-check"),
        ("task", "test-all"),
        ("task", "test-filtered"),
    }
)

_REDIRECT_TOKEN_RE = re.compile(r"^(\d*)>{1,2}(.+)$")
_REDIRECT_OP_ONLY_RE = re.compile(r"^(\d*)>{1,2}$")
_FD_REDIRECT_RE = re.compile(r"^\d*>{1,2}&")
_TRAILING_SHELL_CLOSERS = frozenset({")", "`", "}", "'", '"', ";", "&", "|"})
_SHELL_VAR_RE = re.compile(r"\$\{[A-Za-z_]|\$[A-Za-z_]")

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

# Every value-taking git global flag, mirroring _command_classification.py's
# _GIT_GLOBAL_FLAG_SPEC (source of truth; kept in sync manually -- this
# module is an independent re-implementation for IL-0 import, see the
# module docstring). A flag missing here is misread by the loop below as a
# 1-token boolean skip, so its value gets mistaken for the git subcommand --
# e.g. `git --namespace refs/foo checkout -- file` previously stopped the
# loop at `refs/foo`, never reaching `checkout`.
_GIT_FLAG_WITH_VALUE: frozenset[str] = frozenset(
    {"-C", "--git-dir", "--work-tree", "-c", "--namespace", "--config-env"}
)

_COMMAND_WRAPPERS: frozenset[str] = frozenset({"command", "nice", "time", "sudo", "nohup"})
_WRAPPERS_WITH_DURATION: frozenset[str] = frozenset({"timeout"})
_WRAPPERS_WITH_SHORT_FLAG: frozenset[str] = frozenset({"stdbuf"})


@dataclass(frozen=True, slots=True)
class _HeredocOpener:
    operator_span: tuple[int, int]
    delimiter: str
    strip_tabs: bool


@dataclass(frozen=True, slots=True)
class _HeredocOccurrence:
    operator_span: tuple[int, int]
    body_deletion_span: tuple[int, int]
    capture_tail_span: tuple[int, int]


def _is_comment_start(command: str, index: int, line_start: int) -> bool:
    if index == line_start:
        return True
    previous = command[index - 1]
    return previous.isspace() or previous in ";|&("


def _quoted_delimiter_fragment(command: str, start: int, line_end: int) -> tuple[str, int] | None:
    quote = command[start]
    index = start + 1
    parts: list[str] = []
    while index < line_end:
        char = command[index]
        if char == quote:
            return ("".join(parts), index + 1)
        if quote == '"' and char == "\\" and index + 1 < line_end:
            parts.append(command[index + 1])
            index += 2
        else:
            parts.append(char)
            index += 1
    return None


def _parse_heredoc_delimiter(command: str, start: int, line_end: int) -> tuple[str, int] | None:
    """Parse one shell word after a heredoc operator, applying quote removal."""
    parts: list[str] = []
    index = start
    while index < line_end:
        char = command[index]
        if char.isspace() or char in ";|&()<>":
            break
        if char == "\\":
            if index + 1 >= line_end:
                return None
            parts.append(command[index + 1])
            index += 2
            continue
        if char in "'\"":
            fragment = _quoted_delimiter_fragment(command, index, line_end)
            if fragment is None:
                return None
            text, index = fragment
            parts.append(text)
            continue
        parts.append(char)
        index += 1
    return ("".join(parts), index) if parts else None


def _skip_shell_quote(command: str, start: int, line_end: int) -> int:
    quote = command[start]
    index = start + 1
    while index < line_end:
        if command[index] == quote:
            return index + 1
        if quote == '"' and command[index] == "\\" and index + 1 < line_end:
            index += 2
        else:
            index += 1
    return line_end


def _skip_arithmetic(command: str, start: int, line_end: int) -> int:
    depth = 1
    index = start + 3
    while index < line_end:
        if command.startswith("$((", index):
            depth += 1
            index += 3
            continue
        if command.startswith("))", index):
            depth -= 1
            index += 2
            if not depth:
                return index
            continue
        index += 2 if command[index] == "\\" and index + 1 < line_end else 1
    return line_end


def _skip_non_heredoc_syntax(command: str, index: int, line_end: int) -> int | None:
    if command[index] == "\\":
        return min(index + 2, line_end)
    if command[index] in "'\"":
        return _skip_shell_quote(command, index, line_end)
    if command.startswith("$((", index):
        return _skip_arithmetic(command, index, line_end)
    return None


def _heredoc_openers_on_line(command: str, start: int, line_end: int) -> list[_HeredocOpener]:
    """Find unquoted heredoc redirect operators on one command line."""
    openers: list[_HeredocOpener] = []
    index = start
    while index < line_end:
        char = command[index]
        skipped = _skip_non_heredoc_syntax(command, index, line_end)
        if skipped is not None:
            index = skipped
            continue
        if char == "#" and _is_comment_start(command, index, start):
            break
        if not command.startswith("<<", index):
            index += 1
            continue
        if command.startswith("<<<", index):
            index += 3
            continue

        operator_end = index + 2
        strip_tabs = operator_end < line_end and command[operator_end] == "-"
        if strip_tabs:
            operator_end += 1
        delimiter_start = operator_end
        while delimiter_start < line_end and command[delimiter_start] in " \t":
            delimiter_start += 1
        parsed = _parse_heredoc_delimiter(command, delimiter_start, line_end)
        if parsed is None:
            index = operator_end
            continue
        delimiter, delimiter_end = parsed
        openers.append(
            _HeredocOpener(
                operator_span=(index, delimiter_end),
                delimiter=delimiter,
                strip_tabs=strip_tabs,
            )
        )
        index = delimiter_end
    return openers


def _heredoc_terminator(
    command: str, start: int, delimiter: str, *, strip_tabs: bool
) -> tuple[int, int, int, int] | None:
    """Return raw/rendered terminator starts, its end, and the next line start."""
    line_start = start
    while line_start < len(command):
        line_end = command.find("\n", line_start)
        content_end = len(command) if line_end < 0 else line_end
        rendered_start = line_start
        if strip_tabs:
            while rendered_start < content_end and command[rendered_start] == "\t":
                rendered_start += 1
        delimiter_end = rendered_start + len(delimiter)
        if (
            command.startswith(delimiter, rendered_start)
            and delimiter_end <= content_end
            and all(char in " \t" for char in command[delimiter_end:content_end])
        ):
            return (
                line_start,
                rendered_start,
                delimiter_end,
                content_end + 1 if line_end >= 0 else content_end,
            )
        if line_end < 0:
            break
        line_start = line_end + 1
    return None


def _heredoc_occurrences(command: str) -> list[_HeredocOccurrence]:
    """Collect heredoc deletion spans without treating their contents as commands."""
    occurrences: list[_HeredocOccurrence] = []
    position = 0
    while position < len(command):
        line_end = command.find("\n", position)
        if line_end < 0:
            break
        openers = _heredoc_openers_on_line(command, position, line_end)
        if not openers:
            position = line_end + 1
            continue

        pending: list[tuple[_HeredocOpener, tuple[int, int]]] = []
        body_start = line_end + 1
        next_position = body_start
        capture_tail_end = len(command)
        for opener in openers:
            terminator = _heredoc_terminator(
                command, body_start, opener.delimiter, strip_tabs=opener.strip_tabs
            )
            if terminator is None:
                pending.append((opener, (body_start, len(command))))
                capture_tail_end = len(command)
                next_position = len(command)
                break

            _raw_start, rendered_start, delimiter_end, next_position = terminator
            pending.append((opener, (body_start, rendered_start)))
            body_start = next_position
            capture_tail_end = delimiter_end

        capture_tail_span = (line_end, capture_tail_end)
        occurrences.extend(
            _HeredocOccurrence(
                operator_span=opener.operator_span,
                body_deletion_span=body_deletion_span,
                capture_tail_span=capture_tail_span,
            )
            for opener, body_deletion_span in pending
        )
        position = next_position
    return occurrences


def _render_replacements(command: str, replacements: list[tuple[int, int, str]]) -> str:
    rendered: list[str] = []
    position = 0
    for start, end, value in sorted(replacements):
        rendered.append(command[position:start])
        rendered.append(value)
        position = end
    rendered.append(command[position:])
    return "".join(rendered)


_ENV_VALUE_FLAGS: frozenset[str] = frozenset(
    {"-u", "--unset", "-C", "--chdir", "-S", "--split-string", "--argv0"}
)

_WRAPPER_VALUE_FLAGS_DETACHED: frozenset[str] = frozenset(
    {
        "-u",
        "--user",
        "-n",
        "--adjustment",
        "-k",
        "--kill-after",
        "-s",
        "--signal",
        "-g",
        "--group",
        "-p",
        "--priority",
    }
)


def _resolve_write_target(path: str, cwd: str = "") -> str | None:
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


def _strip_heredoc_bodies(command: str) -> str:
    return _render_replacements(
        command,
        [(*occurrence.body_deletion_span, "") for occurrence in _heredoc_occurrences(command)],
    )


def _tokenizer_heredoc_projection(command: str) -> str:
    """Remove heredoc syntax and bodies before shell tokenization."""
    occurrences = _heredoc_occurrences(command)
    replacements = [(*occurrence.operator_span, "") for occurrence in occurrences]
    replacements.extend(
        (*span, "") for span in {occurrence.capture_tail_span for occurrence in occurrences}
    )
    return _render_replacements(command, replacements)


def _normalize_newlines(command: str) -> str:
    result: list[str] = []
    in_single = False
    in_double = False
    i = 0
    while i < len(command):
        c = command[i]
        if c == "\\" and not in_single and i + 1 < len(command):
            result.append(c)
            result.append(command[i + 1])
            i += 2
            continue
        if c == "'" and not in_double:
            in_single = not in_single
        elif c == '"' and not in_single:
            in_double = not in_double
        elif c == "\n" and not in_single and not in_double:
            result.append(" ; ")
            i += 1
            continue
        result.append(c)
        i += 1
    return "".join(result)


def _tokenize_command_segments(command: str) -> list[list[str]]:
    try:
        lexer = shlex.shlex(
            _normalize_newlines(_tokenizer_heredoc_projection(command)),
            posix=True,
            punctuation_chars=";&|",
        )
        lexer.whitespace_split = True
        tokens = list(lexer)
    except (ValueError, TypeError):
        return []
    segments: list[list[str]] = []
    current: list[str] = []
    for token in tokens:
        if token in _SPLIT_TOKENS:
            if current:
                segments.append(current)
                current = []
        else:
            current.append(token)
    if current:
        segments.append(current)
    return segments


def contains_test_gate_command(
    command: str,
    configured_commands: Iterable[Sequence[str]] = (),
) -> bool:
    """Return whether a Bash command invokes a managed test gate."""
    gate_commands = _CANONICAL_TEST_GATE_COMMANDS | {
        tuple(candidate) for candidate in configured_commands if candidate
    }
    for segment in _tokenize_command_segments(command):
        normalized = tuple(segment)
        while normalized and "=" in normalized[0] and not normalized[0].startswith(("/", "./")):
            normalized = normalized[1:]
        if any(normalized[: len(gate)] == gate for gate in gate_commands):
            return True
        if normalized[:2] == ("uv", "run"):
            normalized = normalized[2:]
        if any(normalized[: len(gate)] == gate for gate in gate_commands):
            return True
        if normalized[:1] == ("pytest",):
            return True
        if (
            len(normalized) >= 3
            and normalized[0].startswith("python")
            and normalized[1:3] == ("-m", "pytest")
        ):
            return True
    return False


def _redirect_operand(tokens: Sequence[str], index: int) -> tuple[str | None, int] | None:
    """Return a redirect operand and the number of tokens it occupies."""
    token = tokens[index]
    if token in (">", ">>") or _REDIRECT_OP_ONLY_RE.match(token):
        if index + 1 < len(tokens):
            return tokens[index + 1], 2
        return None, 1
    match = _REDIRECT_TOKEN_RE.match(token)
    if match is not None:
        return match.group(2), 1
    return None


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
        operand = _redirect_operand(tokens, i)
        if operand is None:
            i += 1
            continue
        path, consumed = operand
        if path is not None:
            while path and path[-1] in _TRAILING_SHELL_CLOSERS:
                path = path[:-1]
            resolved = _resolve_write_target(path, cwd)
            if resolved is not None:
                targets.append(resolved)
        i += consumed
    return targets


def _is_posix_assignment(token: str) -> bool:
    if "=" not in token or token.startswith("="):
        return False
    name, _sep, _value = token.partition("=")
    if not name:
        return False
    if not (name[0].isalpha() or name[0] == "_"):
        return False
    return all(c.isalnum() or c == "_" for c in name)


def _consume_wrapper_options(start: int, segment: list[str]) -> int:
    """Skip past a wrapper's attached/detached value options.

    Wrappers (sudo, nice, nohup, time, command) accept option flags before
    the inner command. Many take values either attached (--user=root) or as
    the next detached token (-u root). Returns the index of the first
    non-option token.
    """
    i = start
    while i < len(segment):
        token = segment[i]
        if token == "--":
            return i + 1
        if not token.startswith("-"):
            return i
        if "=" in token:
            i += 1
            continue
        if token in _WRAPPER_VALUE_FLAGS_DETACHED and i + 1 < len(segment):
            i += 2
            continue
        i += 1
    return i


def _consume_env_options_and_assignments(start: int, segment: list[str]) -> int:
    """Skip the options and assignments that precede an ``env`` command."""
    while start < len(segment):
        token = segment[start]
        if token in _ENV_VALUE_FLAGS and start + 1 < len(segment):
            start += 2
        elif token.startswith("-") or "=" in token:
            start += 1
        else:
            break
    return start


def _command_start_index(segment: list[str]) -> int | None:
    if not segment:
        return None
    start = 0
    while start < len(segment):
        token = segment[start]
        if _is_posix_assignment(token):
            start += 1
            continue
        if token == "env":
            start = _consume_env_options_and_assignments(start + 1, segment)
            continue
        if token in _COMMAND_WRAPPERS:
            start = _consume_wrapper_options(start + 1, segment)
            continue
        if token in _WRAPPERS_WITH_DURATION and start + 1 < len(segment):
            start += 2
            continue
        if (
            token in _WRAPPERS_WITH_SHORT_FLAG
            and start + 1 < len(segment)
            and segment[start + 1].startswith("-")
        ):
            start += 2
            continue
        break
    return start if start < len(segment) else None


def _extract_git_targets(segment: list[str], cwd: str) -> list[str] | None:
    targets: list[str] = []
    idx = 1
    while idx < len(segment):
        token = segment[idx]
        if token in _GIT_FLAG_WITH_VALUE:
            idx += 2
            if idx >= len(segment):
                break
        elif token.startswith("-") and "=" not in token and token not in ("--", "--hard"):
            idx += 1
        else:
            break
    if idx >= len(segment):
        return None
    subcmd = segment[idx]
    if subcmd == "checkout" and "--" in segment[idx + 1 :]:
        double_dash = segment.index("--", idx + 1)
        for token in segment[double_dash + 1 :]:
            resolved = _resolve_write_target(token, cwd)
            if resolved is not None and resolved not in _PSEUDO_DEVICE_PATHS:
                targets.append(resolved)
        return targets
    if subcmd == "reset" and "--hard" in segment[idx + 1 :]:
        return targets
    return None


def _resolve_write_verb_operands(
    operands: Sequence[str], cwd: str, *, stop_after_first_resolved: bool = False
) -> list[str]:
    """Resolve write operands, optionally stopping at the first valid path."""
    targets: list[str] = []
    for operand in operands:
        resolved = _resolve_write_target(operand, cwd)
        if resolved is None:
            continue
        if resolved not in _PSEUDO_DEVICE_PATHS:
            targets.append(resolved)
        if stop_after_first_resolved:
            break
    return targets


def _extract_write_verb_targets(verb: str, segment: list[str], cwd: str) -> list[str]:
    operands: list[str] = []
    index = 1
    while index < len(segment):
        redirect = _redirect_operand(segment, index)
        if redirect is not None:
            _, consumed = redirect
            index += consumed
            continue
        token = segment[index]
        if (
            not token.startswith("-")
            and not token.startswith("&")
            and not _FD_REDIRECT_RE.match(token)
        ):
            operands.append(token)
        index += 1

    if verb == "sed":
        flags = [token for token in segment[1:] if token.startswith("-")]
        has_inplace = any(token.startswith("-i") or token == "--in-place" for token in flags)
        return _resolve_write_verb_operands(operands[-1:] if has_inplace else (), cwd)
    if verb in ("mv", "cp"):
        return _resolve_write_verb_operands(operands[-1:] if len(operands) >= 2 else (), cwd)
    if verb == "patch":
        return _resolve_write_verb_operands(operands, cwd, stop_after_first_resolved=True)
    return _resolve_write_verb_operands(operands, cwd)


def _extract_segment_targets(segment: list[str], cwd: str) -> list[str] | None:
    start = _command_start_index(segment)
    if start is None:
        return None
    command = segment[start:]
    verb = command[0]
    if verb == "gh":
        return None
    if verb == "git":
        return _extract_git_targets(command, cwd)
    if verb in _WRITE_VERBS:
        return _extract_write_verb_targets(verb, command, cwd)
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
        flat_tokens = shlex.split(_strip_heredoc_bodies(command))
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
