"""Output-redirect partitioning for hook command classification."""

from __future__ import annotations

import os
import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from autoskillit.hooks._classification._tokenizer import ArgvToken


_REDIRECT_TOKEN_RE = re.compile(r"^(\d*)>{1,2}(.+)$")
_REDIRECT_OP_ONLY_RE = re.compile(r"^(\d*)>{1,2}$")
_FD_DUPLICATION_RE = re.compile(r"^\d*>{1,2}&(?:\d+|-)$")
_FD_TARGET_RE = re.compile(r"^&(?:\d+|-)$")
_SHELL_NAME_RE = re.compile(r"[A-Za-z_][A-Za-z_0-9]*")


def _shell_tilde_prefix(source: str) -> tuple[str, str, int] | None:
    if not source.startswith("~"):
        return "", "", 0
    prefix = source.split("/", 1)[0]
    if not re.fullmatch(r"~[A-Za-z_0-9.-]*", prefix):
        return None
    home = os.path.expanduser(prefix)
    if home == prefix or not os.path.isabs(home):
        return None
    return prefix, home, len(prefix)


def _shell_escaped_char(source: str, index: int, quote: str) -> str | None:
    if index + 1 >= len(source) or source[index + 1] == "\n":
        return None
    escaped = source[index + 1]
    if quote == '"' and escaped not in '$`"\\':
        return "\\" + escaped
    return escaped


def _shell_variable(source: str, index: int, quote: str) -> tuple[str, str, int] | None:
    if source.startswith("${", index):
        closing = source.find("}", index + 2)
        if closing < 0 or _SHELL_NAME_RE.fullmatch(source[index + 2 : closing]) is None:
            return None
        end = closing + 1
    else:
        match = _SHELL_NAME_RE.match(source, index + 1)
        if match is None:
            if index + 1 < len(source) and source[index + 1] in "[(@*#?$!'\"-0123456789":
                return None
            return "$", "$", index + 1
        end = match.end()
    spelling = source[index:end]
    value = os.path.expandvars(spelling)
    if value == spelling or (quote != '"' and any(c in value for c in " \t\n*?[]")):
        return None
    return spelling, value, end


def _unsafe_shell_literal(char: str, quote: str) -> bool:
    return (char == "`" and quote != "'") or (not quote and (char.isspace() or char in ";&|<>"))


def _expand_shell_target(path: str, source: str) -> str | None:
    """Expand only shell syntax whose spelling is proven by the source span."""
    tilde = _shell_tilde_prefix(source)
    if tilde is None:
        return None
    prefix, home, index = tilde
    cooked = [prefix]
    expanded = [home]
    quote = ""

    while index < len(source):
        char = source[index]
        if char in "'\"" and quote in ("", char):
            quote = "" if quote else char
            index += 1
            continue
        if char == "\\" and quote != "'":
            escaped = _shell_escaped_char(source, index, quote)
            if escaped is None:
                return None
            cooked.append(escaped)
            expanded.append(escaped)
            index += 2
            continue
        if char == "$" and quote != "'":
            variable = _shell_variable(source, index, quote)
            if variable is None:
                return None
            spelling, value, index = variable
            cooked.append(spelling)
            expanded.append(value)
            continue
        if _unsafe_shell_literal(char, quote):
            return None
        cooked.append(char)
        expanded.append(char)
        index += 1

    if quote or "".join(cooked) != path:
        return None
    return "".join(expanded)


def resolve_write_target(
    path: str, cwd: str = "", *, shell_source: str | None = None
) -> str | None:
    """Resolve a literal argv path or a shell word with its source spelling."""
    if not path:
        return None
    if shell_source is not None:
        expanded = _expand_shell_target(path, shell_source)
        if expanded is None or not expanded:
            return None
        path = expanded
    if os.path.isabs(path):
        return path
    if cwd:
        return os.path.join(cwd, path)
    return None


def _consume_output_redirect(
    tokens: Sequence[str],
    syntax: Sequence[bool],
    index: int,
    argv_tokens: Sequence[ArgvToken] | None,
) -> tuple[int, str | None, int] | None:
    """Consume one active output redirect, returning its next index, target, and count."""
    token = tokens[index]
    if not syntax[index]:
        return None
    if token.startswith((">(", "<(")):
        return None
    if _FD_DUPLICATION_RE.fullmatch(token):
        return (index + 1, None, 0)
    if _REDIRECT_OP_ONLY_RE.fullmatch(token):
        next_index = index + 1
        if next_index < len(tokens) and not (
            syntax[next_index]
            and (
                _REDIRECT_OP_ONLY_RE.fullmatch(tokens[next_index])
                or _FD_DUPLICATION_RE.fullmatch(tokens[next_index])
            )
        ):
            target = tokens[next_index]
            if _FD_TARGET_RE.fullmatch(target) and (
                argv_tokens is None or argv_tokens[next_index].raw_span.lstrip() == target
            ):
                return (next_index + 1, None, 0)
            if target.startswith((">(", "<(")):
                return (next_index + 1, None, 0)
            return (next_index + 1, target, 1)
        return (next_index, None, 1)
    match = _REDIRECT_TOKEN_RE.fullmatch(token)
    if match is None:
        return None
    return (index + 1, match.group(2), 1)


@dataclass(frozen=True, slots=True)
class OutputRedirectPartition:
    """Result of partitioning a token stream by output-redirect syntax."""

    segments: list[int]
    """Indices into the original ``tokens`` list that survive as executable argv."""

    targets: list[str]
    """Resolved file-redirect target paths, anchored against ``cwd`` when relative."""

    file_redirect_count: int
    """Number of file-redirects encountered (``>>N``-style FD duplications excluded)."""

    unresolved: bool
    """True when at least one redirect target could not be resolved to a concrete path."""


def _partition_output_redirect_indices(
    tokens: Sequence[str],
    *,
    cwd: str,
    redirect_syntax: Sequence[bool],
    argv_tokens: Sequence[ArgvToken] | None = None,
) -> OutputRedirectPartition:
    """Core of _partition_output_redirects: which *tokens* indices are executable argv.

    Shared so callers can project index-aligned argv tokens onto the same
    partitioning decision without re-deriving redirect syntax.
    """
    segments: list[int] = []
    targets: list[str] = []
    file_redirect_count = 0
    unresolved = False
    i = 0
    while i < len(tokens):
        redirect = _consume_output_redirect(tokens, redirect_syntax, i, argv_tokens)
        if redirect is None:
            segments.append(i)
            i += 1
            continue
        redirect_index = i
        i, target, file_redirect_delta = redirect
        file_redirect_count += file_redirect_delta

        if target is not None:
            shell_source = None
            if argv_tokens is not None:
                shell_source = argv_tokens[i - 1].raw_span.lstrip()
                if i == redirect_index + 1:
                    operator = tokens[redirect_index][: -len(target)]
                    shell_source = (
                        shell_source[len(operator) :] if shell_source.startswith(operator) else ""
                    )
            resolved = resolve_write_target(target, cwd, shell_source=shell_source)
            if resolved is not None:
                targets.append(resolved)
            else:
                unresolved = True
        elif file_redirect_delta:
            unresolved = True
    return OutputRedirectPartition(
        segments=segments,
        targets=targets,
        file_redirect_count=file_redirect_count,
        unresolved=unresolved,
    )


def _partition_output_redirects(
    tokens: Sequence[str],
    *,
    cwd: str,
    redirect_syntax: Sequence[bool],
    argv_tokens: Sequence[ArgvToken] | None = None,
) -> tuple[list[str], list[str], int]:
    """Separate depth-zero output control from executable argv."""
    partition = _partition_output_redirect_indices(
        tokens, cwd=cwd, redirect_syntax=redirect_syntax, argv_tokens=argv_tokens
    )
    return (
        [tokens[i] for i in partition.segments],
        partition.targets,
        partition.file_redirect_count,
    )


def _select_executable_argv_tokens(
    tokens: Sequence[str],
    argv_tokens: Sequence[ArgvToken],
    *,
    cwd: str,
    redirect_syntax: Sequence[bool],
) -> list[ArgvToken]:
    """Project *argv_tokens* onto the same indices _partition_output_redirects

    keeps as executable argv -- for callers threading ArgvToken quote
    provenance through the same redirect-partitioning decision.
    """
    partition = _partition_output_redirect_indices(
        tokens, cwd=cwd, redirect_syntax=redirect_syntax, argv_tokens=argv_tokens
    )
    return [argv_tokens[i] for i in partition.segments]


def extract_redirect_targets_with_status(
    tokens: list[str],
    cwd: str = "",
    *,
    redirect_syntax: Sequence[bool],
    argv_tokens: Sequence[ArgvToken] | None = None,
) -> tuple[list[str], bool]:
    """Return redirect targets and whether an output target was unresolved."""
    partition = _partition_output_redirect_indices(
        tokens, cwd=cwd, redirect_syntax=redirect_syntax, argv_tokens=argv_tokens
    )
    return partition.targets, partition.unresolved
