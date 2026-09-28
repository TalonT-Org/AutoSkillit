"""Quote-aware pre-lex rewrites that carry original source positions."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from autoskillit.hooks._classification import _substitution_scanning
    from autoskillit.hooks._classification._source_map import (
        SourceMapBuilder,
        SourceMappedText,
    )
elif __package__:
    from . import _source_map, _substitution_scanning

    SourceMapBuilder = _source_map.SourceMapBuilder
    SourceMappedText = _source_map.SourceMappedText
else:
    import _source_map
    import _substitution_scanning

    SourceMapBuilder = _source_map.SourceMapBuilder
    SourceMappedText = _source_map.SourceMappedText

_extract_process_substitution_occurrences = (
    _substitution_scanning._extract_process_substitution_occurrences
)
_iter_substitution_occurrences = _substitution_scanning._iter_substitution_occurrences


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


def _render_replacements(
    source: SourceMappedText, replacements: list[tuple[int, int, str]]
) -> SourceMappedText:
    builder = SourceMapBuilder(source)
    position = 0
    for start, end, value in sorted(replacements):
        builder.copy(position, start)
        if value:
            builder.emit(value, start, end)
        position = end
    builder.copy(position, len(source.text))
    return builder.build()


def _mask_substitutions(
    source: SourceMappedText,
) -> tuple[SourceMappedText, dict[str, str]] | None:
    """Keep each active substitution intact as one part of a shell word."""
    command = source.text
    spans: list[tuple[int, int]] = []
    for body_start, body in _iter_substitution_occurrences(command):
        quote = command[body_start - 1]
        start = body_start - (1 if quote == "`" else 2)
        end = body_start + len(body) + 1
        if end > len(command) or command[end - 1] != ("`" if quote == "`" else ")"):
            return None
        spans.append((start, end))
    for _kind, start, end, _body, balanced in _extract_process_substitution_occurrences(command):
        if not balanced:
            return None
        spans.append((start, end))

    replacements: list[tuple[int, int, str]] = []
    originals: dict[str, str] = {}
    covered_until = 0
    for start, end in sorted(spans, key=lambda span: (span[0], -span[1])):
        if start < covered_until:
            continue
        marker = f"__AUTOSKILLIT_SUBSTITUTION_{len(originals)}__"
        while marker in command:
            marker += "_"
        originals[marker] = command[start:end]
        replacements.append((start, end, marker))
        covered_until = end
    return _render_replacements(source, replacements), originals


def _normalize_newlines_for_tokenize(source: SourceMappedText) -> SourceMappedText:
    """Make bare newlines command boundaries while preserving quoted newlines."""
    command = source.text
    builder = SourceMapBuilder(source)
    in_single = False
    in_double = False
    i = 0
    while i < len(command):
        c = command[i]
        if c == "\\" and not in_single and i + 1 < len(command):
            if command[i + 1] == "\n":
                i += 2
                continue
            builder.copy(i, i + 2)
            i += 2
            continue
        if c == "'" and not in_double:
            in_single = not in_single
        elif c == '"' and not in_single:
            in_double = not in_double
        elif c == "\n" and not in_single and not in_double:
            builder.emit(" ; \n", i, i + 1)
            i += 1
            continue
        builder.copy(i, i + 1)
        i += 1
    return builder.build()


def _output_redirect_end(command: str, start: int) -> int | None:
    i = start
    char = command[i]
    if char.isdecimal() and (i == 0 or command[i - 1].isspace() or command[i - 1] in ";&|("):
        while i < len(command) and command[i].isdecimal():
            i += 1
        if i >= len(command) or command[i] != ">":
            return None
    elif char != ">":
        return None

    operator_end = i + 1
    if operator_end < len(command) and command[operator_end] == ">":
        operator_end += 1
    if operator_end < len(command) and command[operator_end] == "(":
        return None
    if (
        operator_end < len(command)
        and command[operator_end] == "&"
        and (not command[start:i] or command[start:i].isdecimal())
    ):
        fd_end = operator_end + 1
        while fd_end < len(command) and command[fd_end].isdecimal():
            fd_end += 1
        if fd_end == operator_end + 1:
            return None
        operator_end = fd_end
    return operator_end


def _mark_unquoted_output_redirects(
    source: SourceMappedText,
) -> tuple[SourceMappedText, dict[str, str]]:
    """Replace recognized redirect operators with shlex-stable placeholders."""
    command = source.text
    builder = SourceMapBuilder(source)
    redirects: dict[str, str] = {}
    in_single = False
    in_double = False
    i = 0
    while i < len(command):
        char = command[i]
        if char == "\\" and not in_single and i + 1 < len(command):
            builder.copy(i, i + 2)
            i += 2
            continue
        if char == "'" and not in_double:
            in_single = not in_single
            builder.copy(i, i + 1)
            i += 1
            continue
        if char == '"' and not in_single:
            in_double = not in_double
            builder.copy(i, i + 1)
            i += 1
            continue
        if in_single or in_double:
            builder.copy(i, i + 1)
            i += 1
            continue

        operator_end = _output_redirect_end(command, i)
        if operator_end is None:
            builder.copy(i, i + 1)
            i += 1
            continue

        marker = f"__AUTOSKILLIT_REDIRECT_{len(redirects)}__"
        redirects[marker] = command[i:operator_end]
        builder.emit(f" {marker} ", i, operator_end)
        i = operator_end
    return builder.build(), redirects


class _GroupingScan:
    def __init__(self, source: SourceMappedText) -> None:
        self.command = source.text
        self.builder = SourceMapBuilder(source)
        self.groups: dict[str, tuple[str, int]] = {}
        self.stack: list[tuple[str, int]] = []
        self.case_modes: list[str] = []
        self.command_position: bool = True
        self.previous_words: list[str] = []
        self.index: int = 0

    def _consume_trivia(self) -> bool:
        command = self.command
        i = self.index
        char = command[i]
        if char == "\\" and i + 1 < len(command):
            self.builder.copy(i, i + 2)
            self.command_position = False
            self.index += 2
            return True
        if char in "'\"":
            self.index = _skip_shell_quote(command, i, len(command))
            self.builder.copy(i, self.index)
            self.command_position = False
            return True
        if char == "#" and (i == 0 or command[i - 1].isspace() or command[i - 1] in ";|&("):
            # Keep grouping markers out of comments; shlex still decides their tokenization.
            end = command.find("\n", i)
            self.index = len(command) if end < 0 else end
            self.builder.copy(i, self.index)
            return True
        if char.isspace():
            self.builder.copy(i, i + 1)
            if char == "\n":
                self.command_position = True
                self.previous_words.clear()
            self.index += 1
            return True
        if char in ";|&":
            end = i + 1
            while end < len(command) and command[end] == char:
                end += 1
            self.builder.copy(i, end)
            if char == ";" and end - i >= 2 and self.case_modes:
                self.case_modes[-1] = "pattern"
            self.command_position = True
            self.previous_words.clear()
            self.index = end
            return True
        return False

    def _consume_delimiter(self) -> bool | None:
        command = self.command
        i = self.index
        char = command[i]
        words = self.previous_words
        function_body = bool(
            words
            and (
                re.fullmatch(r"[A-Za-z_][A-Za-z_0-9]*\(\)", words[-1])
                or (len(words) >= 2 and words[-2] == "function")
            )
        )
        is_open = (
            char == "("
            and self.command_position
            and not command.startswith("((", i)
            and not (self.case_modes and self.case_modes[-1] == "pattern")
        ) or (
            char == "{"
            and (self.command_position or function_body)
            and i + 1 < len(command)
            and command[i + 1].isspace()
        )
        is_case_end = char == ")" and bool(self.case_modes) and self.case_modes[-1] == "pattern"
        is_close = bool(self.stack) and (
            char == ")"
            and self.stack[-1][0] == "("
            and not is_case_end
            or char == "}"
            and self.stack[-1][0] == "{"
            and self.command_position
        )
        if char == "}" and self.command_position and not is_close:
            return None
        if not (is_open or is_close or is_case_end):
            return False
        if is_case_end:
            self.case_modes[-1] = "body"
            kind, group_id = "case", 0
        elif is_open:
            group_id = len(self.groups) + 1 if char == "(" else 0
            self.stack.append((char, group_id))
            kind = (
                "open_subshell"
                if char == "("
                else "open_function"
                if function_body
                else "open_brace"
            )
        else:
            opener, group_id = self.stack.pop()
            kind = "close_subshell" if opener == "(" else "close_brace"
        marker = f"__AUTOSKILLIT_GROUP_{len(self.groups)}__"
        while marker in command:
            marker += "_"
        self.groups[marker] = (kind, group_id)
        self.builder.emit(f" {marker} ", i, i + 1)
        self.command_position = is_open or is_case_end
        self.previous_words.clear()
        self.index += 1
        return True

    def _consume_word(self) -> bool:
        command = self.command
        i = self.index
        if command.startswith("((", i) and self.command_position:
            end = command.find("))", i + 2)
            if end < 0:
                return False
            self.builder.copy(i, end + 2)
            self.command_position = False
            self.index = end + 2
            return True
        function_name = re.match(r"[A-Za-z_][A-Za-z_0-9]*\(\)", command[i:])
        if function_name is not None and self.command_position:
            end = i + len(function_name.group())
        else:
            end = i + 1
            while (
                end < len(command)
                and not command[end].isspace()
                and command[end] not in ";|&(){}'\""
            ):
                end += 2 if command[end] == "\\" and end + 1 < len(command) else 1
        word = command[i:end]
        self.builder.copy(i, end)
        if word == "case" and self.command_position:
            self.case_modes.append("header")
        elif word == "in" and self.case_modes and self.case_modes[-1] == "header":
            self.case_modes[-1] = "pattern"
        elif word == "esac" and self.case_modes:
            self.case_modes.pop()
        self.previous_words.append(word)
        self.command_position = word in {
            "if",
            "then",
            "else",
            "elif",
            "while",
            "until",
            "do",
            "time",
            "!",
        }
        self.index = end
        return True


def _mark_grouping_delimiters(
    source: SourceMappedText,
) -> tuple[SourceMappedText, dict[str, tuple[str, int]]] | None:
    """Separate active shell groups from argv before shlex sees their punctuation."""
    scan = _GroupingScan(source)
    while scan.index < len(scan.command):
        if scan._consume_trivia():
            continue
        delimiter = scan._consume_delimiter()
        if delimiter is None:
            return None
        if delimiter:
            continue
        if not scan._consume_word():
            return None
    return (scan.builder.build(), scan.groups) if not scan.stack else None
