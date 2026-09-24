"""Quote-aware substitution masking and shell grouping for the hook tokenizer."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from autoskillit.hooks._classification import _substitution_scanning
elif __package__:
    from . import _substitution_scanning
else:
    import _substitution_scanning

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


def _render_replacements(command: str, replacements: list[tuple[int, int, str]]) -> str:
    rendered: list[str] = []
    position = 0
    for start, end, value in sorted(replacements):
        rendered.append(command[position:start])
        rendered.append(value)
        position = end
    rendered.append(command[position:])
    return "".join(rendered)


def _mask_substitutions(command: str) -> tuple[str, dict[str, str]] | None:
    """Keep each active substitution intact as one part of a shell word."""
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
    return _render_replacements(command, replacements), originals


class _GroupingScan:
    def __init__(self, command: str) -> None:
        self.command = command
        self.rendered: list[str] = []
        self.groups: dict[str, tuple[str, int]] = {}
        self.stack: list[tuple[str, int]] = []
        self.case_modes: list[str] = []
        self.command_position = True
        self.previous_words: list[str] = []
        self.index = 0

    def _consume_trivia(self) -> bool:
        command = self.command
        i = self.index
        char = command[i]
        if char == "\\" and i + 1 < len(command):
            self.rendered.append(command[i : i + 2])
            self.command_position = False
            self.index += 2
            return True
        if char in "'\"":
            self.index = _skip_shell_quote(command, i, len(command))
            self.rendered.append(command[i : self.index])
            self.command_position = False
            return True
        if char == "#" and (i == 0 or command[i - 1].isspace() or command[i - 1] in ";|&("):
            end = command.find("\n", i)
            self.index = len(command) if end < 0 else end
            self.rendered.append(command[i : self.index])
            return True
        if char.isspace():
            self.rendered.append(char)
            if char == "\n":
                self.command_position = True
                self.previous_words.clear()
            self.index += 1
            return True
        if char in ";|&":
            end = i + 1
            while end < len(command) and command[end] == char:
                end += 1
            self.rendered.append(command[i:end])
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
        self.rendered.extend((" ", marker, " "))
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
            self.rendered.append(command[i : end + 2])
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
        self.rendered.append(word)
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


def _mark_grouping_delimiters(command: str) -> tuple[str, dict[str, tuple[str, int]]] | None:
    """Separate active shell groups from argv before shlex sees their punctuation."""
    scan = _GroupingScan(command)
    while scan.index < len(command):
        if scan._consume_trivia():
            continue
        delimiter = scan._consume_delimiter()
        if delimiter is None:
            return None
        if delimiter:
            continue
        if not scan._consume_word():
            return None
    return ("".join(scan.rendered), scan.groups) if not scan.stack else None
