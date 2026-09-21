"""Output-redirect partitioning for hook command classification.

Split out of `_runtime/_command_classification.py` (issue #5120) to keep that
facade under REQ-CNST-010's line cap while making room for the
``OutputRedirectPartition`` dataclass consolidation. Self-contained: the
partition machinery depends only on stdlib (``re``, ``os``) and the
``ArgvToken`` token-type imported from `_tokenizer.py` for the
``_select_executable_argv_tokens`` projection. Re-exported through the
facade's existing block B bootstrap so consumers importing via
``autoskillit.hooks._runtime._command_classification`` (or via the
``autoskillit.hooks._runtime`` facade) continue to work without source
edits.
"""

from __future__ import annotations

import os
import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from autoskillit.hooks._classification._tokenizer import ArgvToken
else:
    if __package__:
        from . import _tokenizer
    else:
        import _tokenizer

    ArgvToken = _tokenizer.ArgvToken


_REDIRECT_TOKEN_RE = re.compile(r"^(\d*)>{1,2}(.+)$")
_REDIRECT_OP_ONLY_RE = re.compile(r"^(\d*)>{1,2}$")
_FD_REDIRECT_RE = re.compile(r"^\d*>{1,2}&")
_FD_DUPLICATION_RE = re.compile(r"^\d*>&\d+$")
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


def _consume_output_redirect(
    tokens: Sequence[str], syntax: Sequence[bool], index: int
) -> tuple[int, str | None, int] | None:
    """Consume one active output redirect, returning its next index, target, and count."""
    token = tokens[index]
    if not syntax[index]:
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
            return (next_index + 1, tokens[next_index], 1)
        return (next_index, None, 1)
    match = _REDIRECT_TOKEN_RE.fullmatch(token)
    if match is None:
        return None
    return (index + 1, match.group(2), 1)


@dataclass(frozen=True, slots=True)
class OutputRedirectPartition:
    """Result of partitioning a token stream by output-redirect syntax.

    Consolidates the prior 4-tuple return shape of
    ``_partition_output_redirect_indices`` so future fields (e.g. an
    ``_unresolved_target`` count for shell-state expansion, a
    ``pseudo_device_count``) land here rather than as further positional
    returns that every caller must unpack.
    """

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
    redirect_syntax: Sequence[bool] | None = None,
) -> OutputRedirectPartition:
    """Core of _partition_output_redirects: which *tokens* indices are executable argv.

    Shared so a caller threading a second, index-aligned parallel array (e.g.
    ArgvToken quote provenance) can project it onto the same partitioning
    decision without re-deriving the redirect-syntax logic independently.
    """
    syntax = redirect_syntax if redirect_syntax is not None else [True] * len(tokens)
    executable_indices: list[int] = []
    targets: list[str] = []
    file_redirect_count = 0
    unresolved_target = False
    depth = 0
    i = 0
    while i < len(tokens):
        token = tokens[i]
        if token == "(" or (token.startswith("(") and len(token) > 1):
            depth += 1
            if token.endswith(")") and len(token) > 1:
                depth -= 1
            executable_indices.append(i)
            i += 1
            continue
        if token == ")":
            if depth > 0:
                depth -= 1
            executable_indices.append(i)
            i += 1
            continue
        if token.endswith(")") and len(token) > 1:
            if depth > 0:
                depth -= 1
            executable_indices.append(i)
            i += 1
            continue
        if depth > 0:
            executable_indices.append(i)
            i += 1
            continue
        redirect = _consume_output_redirect(tokens, syntax, i)
        if redirect is None:
            executable_indices.append(i)
            i += 1
            continue
        i, target, file_redirect_delta = redirect
        file_redirect_count += file_redirect_delta

        if target is not None:
            while target and target[-1] in _TRAILING_SHELL_CLOSERS:
                target = target[:-1]
            resolved = resolve_write_target(target, cwd)
            if resolved is not None:
                targets.append(resolved)
            else:
                unresolved_target = True
        elif file_redirect_delta:
            unresolved_target = True
    return OutputRedirectPartition(
        segments=executable_indices,
        targets=targets,
        file_redirect_count=file_redirect_count,
        unresolved=unresolved_target,
    )


def _partition_output_redirects(
    tokens: Sequence[str],
    *,
    cwd: str,
    redirect_syntax: Sequence[bool] | None = None,
) -> tuple[list[str], list[str], int]:
    """Separate depth-zero output control from executable argv."""
    partition = _partition_output_redirect_indices(
        tokens, cwd=cwd, redirect_syntax=redirect_syntax
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
    redirect_syntax: Sequence[bool] | None = None,
) -> list[ArgvToken]:
    """Project *argv_tokens* onto the same indices _partition_output_redirects

    keeps as executable argv -- for callers threading ArgvToken quote
    provenance through the same redirect-partitioning decision that
    _partition_output_redirects already makes from *tokens* alone.
    """
    partition = _partition_output_redirect_indices(
        tokens, cwd=cwd, redirect_syntax=redirect_syntax
    )
    return [argv_tokens[i] for i in partition.segments]


def extract_redirect_targets(tokens: list[str], cwd: str = "") -> list[str]:
    """Extract resolved redirect target paths from already-tokenized input.

    Returns resolved paths including pseudo-devices — caller filters.
    Relative paths are resolved against cwd when provided.
    """
    return _partition_output_redirects(tokens, cwd=cwd)[1]


def extract_redirect_targets_with_status(
    tokens: list[str], cwd: str = ""
) -> tuple[list[str], bool]:
    """Return redirect targets and whether an output target was unresolved."""
    partition = _partition_output_redirect_indices(tokens, cwd=cwd)
    return partition.targets, partition.unresolved
