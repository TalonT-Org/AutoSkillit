"""Markdown write-cue scanning shared by the skill prose write-target contracts.

A write cue ("write", "save", "output to", ...) collects the inline-code spans that
follow it within its Markdown structure. Fenced blocks, terminal-output report
fields, and NEVER-style "outside X" prohibitions are masked first, so only real
write instructions yield candidate targets.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from typing import NamedTuple

__all__ = ["WRITE_CUE", "CueTarget", "inline_code_spans", "iter_write_cue_targets"]

WRITE_CUE = re.compile(
    r"(?i)\b(write|writes|written|save|saves|saved|output to|output path|"
    r"append|appends|create|creates|emit|store|path:)"
)
_LIST_ITEM = re.compile(r"^(?P<indent>[ \t]*)(?:[-+*]|\d{1,9}[.)])\s+")
_HEADING = re.compile(r"^ {0,3}(#{1,6})(?:\s+|$)(.*)$")
_HORIZONTAL_RULE = re.compile(r"^\s{0,3}(?:(?:\*\s*){3,}|(?:-\s*){3,}|(?:_\s*){3,})$")
_TERMINAL_OUTPUT = re.compile(r"(?i)\boutput\s+to\s+terminal\s*:")
_POLICY_PROHIBITION = re.compile(
    r"(?i)\b(?:never|do\s+not|don't|must\s+not|should\s+not)\s+"
    r"(?:write|writes|written|save|saves|store|output|append|create|emit)\b"
)
_WRITE_VERB = re.compile(
    r"(?i)\b(?:write|writes|written|save|saves|store|output|append|create|emit)\b"
)


def _mask_non_newlines(line: str) -> str:
    return "".join(char if char in "\r\n" else " " for char in line)


def _strip_fenced_blocks(content: str) -> str:
    """Mask Markdown fences while preserving source line numbers."""
    output: list[str] = []
    active_char: str | None = None
    active_length = 0

    for line in content.splitlines(keepends=True):
        text = line.rstrip("\r\n")
        if active_char is not None:
            close = re.match(r"^ {0,3}(`+|~+)[ \t]*$", text)
            output.append(_mask_non_newlines(line))
            if close and close.group(1)[0] == active_char and len(close.group(1)) >= active_length:
                active_char = None
                active_length = 0
            continue

        opening = re.match(r"^ {0,3}(`{3,}|~{3,})(.*)$", text)
        if opening and not (opening.group(1)[0] == "`" and "`" in opening.group(2)):
            active_char = opening.group(1)[0]
            active_length = len(opening.group(1))
            output.append(_mask_non_newlines(line))
        else:
            output.append(line)

    return "".join(output)


def _list_item(line: str) -> tuple[int, int] | None:
    match = _LIST_ITEM.match(line)
    if match is None:
        return None
    return len(match.group("indent").expandtabs(4)), match.end()


def _indent_width(line: str) -> int:
    return len(line) - len(line.lstrip(" \t")) if line.strip() else 0


def _is_heading_or_rule(line: str) -> bool:
    return (
        _HEADING.match(line) is not None or _HORIZONTAL_RULE.match(line.rstrip("\r\n")) is not None
    )


def _mask_terminal_output(lines: list[str]) -> list[str]:
    """Mask terminal report fields before any write cue can collect them."""
    excluded: set[int] = set()
    for index, line in enumerate(lines):
        if not _TERMINAL_OUTPUT.search(line):
            continue
        excluded.add(index)
        scope = _introduced_list_scope(lines, index)
        if scope is not None:
            start, end = scope
            excluded.update(range(start, end + 1))

    return [
        _mask_non_newlines(line) if index in excluded else line for index, line in enumerate(lines)
    ]


def _never_heading(line: str) -> int | None:
    match = _HEADING.match(line)
    if match is None:
        return None
    title = re.sub(r"[*_`]", "", match.group(2)).strip().rstrip(":").strip()
    return len(match.group(1)) if title.casefold().startswith("never") else None


def _is_never_label(line: str) -> bool:
    text = line.strip()
    item = _list_item(text)
    if item is not None:
        text = text[item[1] :].strip()
    text = re.sub(r"[*_`]", "", text).strip().rstrip(":").strip()
    return text.casefold() == "never"


def _mask_never_outside_policies(lines: list[str]) -> list[str]:
    excluded: set[int] = set()
    never_level: int | None = None

    for index, line in enumerate(lines):
        heading = _HEADING.match(line)
        if heading is not None:
            level = len(heading.group(1))
            if never_level is not None and level <= never_level:
                never_level = None
            new_never_level = _never_heading(line)
            if new_never_level is not None:
                never_level = new_never_level
        elif _is_never_label(line):
            never_level = 6

        if "outside" not in line.casefold() or not _WRITE_VERB.search(line):
            continue
        explicit_prohibition = _POLICY_PROHIBITION.search(line) is not None
        if explicit_prohibition or never_level is not None:
            excluded.add(index)

    return [
        _mask_non_newlines(line) if index in excluded else line for index, line in enumerate(lines)
    ]


def inline_code_spans(line: str) -> list[tuple[int, int, str, int]]:
    """Return (start, end, content, delimiter length) for matching backtick spans."""
    spans: list[tuple[int, int, str, int]] = []
    index = 0
    while index < len(line):
        if line[index] != "`":
            index += 1
            continue
        opening_end = index
        while opening_end < len(line) and line[opening_end] == "`":
            opening_end += 1
        delimiter_length = opening_end - index

        cursor = opening_end
        closing: tuple[int, int] | None = None
        while cursor < len(line):
            if line[cursor] != "`":
                cursor += 1
                continue
            run_end = cursor
            while run_end < len(line) and line[run_end] == "`":
                run_end += 1
            if run_end - cursor == delimiter_length:
                closing = (cursor, run_end)
                break
            cursor = run_end

        if closing is None:
            index = opening_end
            continue
        spans.append((index, closing[1], line[opening_end : closing[0]], delimiter_length))
        index = closing[1]

    return spans


def _item_scope_end(lines: list[str], index: int, indent: int) -> int:
    end = index
    for row in range(index + 1, len(lines)):
        line = lines[row]
        if not line.strip():
            continue
        if _is_heading_or_rule(line):
            break
        item = _list_item(line)
        if item is not None:
            if item[0] <= indent:
                break
            end = row
        elif _indent_width(line) > indent:
            end = row
        else:
            break
    return end


def _introduced_list_scope(lines: list[str], index: int) -> tuple[int, int] | None:
    first = index + 1
    while first < len(lines) and not lines[first].strip():
        first += 1
    if first >= len(lines) or _is_heading_or_rule(lines[first]):
        return None
    root = _list_item(lines[first])
    if root is None:
        return None

    root_indent = root[0]
    end = first
    for row in range(first, len(lines)):
        line = lines[row]
        if not line.strip():
            continue
        if _is_heading_or_rule(line):
            break
        item = _list_item(line)
        if item is not None:
            if item[0] < root_indent:
                break
            end = row
        elif _indent_width(line) > root_indent:
            end = row
        else:
            break
    return first, end


def _cue_matches(line: str, spans: list[tuple[int, int, str, int]]) -> list[re.Match[str]]:
    return [
        match
        for match in WRITE_CUE.finditer(line)
        if not any(start <= match.start() < end for start, end, _, _ in spans)
    ]


def _candidate_spans(
    lines: list[str], index: int, cue: re.Match[str], spans: list[tuple[int, int, str, int]]
) -> list[tuple[int, tuple[int, int, str, int]]]:
    """Collect a cue's inline targets within its Markdown structural boundaries."""
    candidates = [(index, span) for span in spans if span[0] >= cue.end()]
    item = _list_item(lines[index])
    item_end = _item_scope_end(lines, index, item[0]) if item is not None else index
    if item is not None:
        for row in range(index, item_end + 1):
            candidates.extend((row, span) for span in inline_code_spans(lines[row]))

    suffix = lines[index][cue.end() :].rstrip()
    introduced = _introduced_list_scope(lines, index) if suffix.endswith(":") else None
    if introduced is not None:
        start, end = introduced
        for row in range(start, end + 1):
            candidates.extend((row, span) for span in inline_code_spans(lines[row]))

    next_line = index + 1
    while next_line < len(lines) and not lines[next_line].strip():
        next_line += 1
    next_allowed = next_line < len(lines) and not _is_heading_or_rule(lines[next_line])
    if item is not None and next_line > item_end:
        next_allowed = False
    if next_allowed:
        next_spans = inline_code_spans(lines[next_line])
        if next_spans:
            candidates.append((next_line, next_spans[0]))
    return candidates


class CueTarget(NamedTuple):
    cue_line: int
    target_line: int
    instruction: str
    target: str
    delimiter_length: int


def iter_write_cue_targets(content: str) -> Iterator[CueTarget]:
    """Yield every inline-code target a write cue collects, with 1-based line numbers."""
    masked = _strip_fenced_blocks(content)
    lines = _mask_terminal_output(masked.splitlines(keepends=True))
    lines = _mask_never_outside_policies(lines)
    for index, line in enumerate(lines):
        spans = inline_code_spans(line)
        for cue in _cue_matches(line, spans):
            instruction = line.rstrip("\r\n").strip()
            for candidate_line, (_, _, target, delimiter_length) in _candidate_spans(
                lines, index, cue, spans
            ):
                yield CueTarget(
                    index + 1, candidate_line + 1, instruction, target, delimiter_length
                )
