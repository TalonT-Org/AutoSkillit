"""Recognize managed test-gate commands in Bash source (stdlib-only, IL-0)."""

from __future__ import annotations

import shlex
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

__all__ = ["contains_test_gate_command"]

_SPLIT_TOKENS: frozenset[str] = frozenset({"&&", "||", ";", "|", "&"})
_CANONICAL_TEST_GATE_COMMANDS: frozenset[tuple[str, ...]] = frozenset(
    {
        ("task", "test-check"),
        ("task", "test-local-gate"),
        ("task", "test-all"),
        ("task", "test-filtered"),
    }
)


@dataclass(frozen=True, slots=True)
class _HeredocOpener:
    operator_span: tuple[int, int]
    delimiter: str
    strip_tabs: bool


@dataclass(frozen=True, slots=True)
class _HeredocOccurrence:
    operator_span: tuple[int, int]
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
    """Collect heredoc syntax spans; body contents are not parsed as commands."""
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

        pending: list[_HeredocOpener] = []
        body_start = line_end + 1
        next_position = body_start
        capture_tail_end = len(command)
        for opener in openers:
            terminator = _heredoc_terminator(
                command, body_start, opener.delimiter, strip_tabs=opener.strip_tabs
            )
            if terminator is None:
                pending.append(opener)
                capture_tail_end = len(command)
                next_position = len(command)
                break

            _raw_start, _rendered_start, delimiter_end, next_position = terminator
            pending.append(opener)
            body_start = next_position
            capture_tail_end = delimiter_end

        capture_tail_span = (line_end, capture_tail_end)
        occurrences.extend(
            _HeredocOccurrence(
                operator_span=opener.operator_span,
                capture_tail_span=capture_tail_span,
            )
            for opener in pending
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


def _remove_heredoc_syntax(command: str) -> str:
    """Erase heredoc operators and bodies, leaving shell tokenizable syntax."""
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
            _normalize_newlines(_remove_heredoc_syntax(command)),
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
