"""Stdlib-only authority for typed skill write scopes.

A skill's ``write_paths`` frontmatter declares one of three kinds:

* ``BOUNDED`` — a non-empty list of project temp directories the skill writes;
* ``UNRESTRICTED`` — the literal ``unrestricted``: the skill writes outside temp;
* ``INHERIT`` — the literal ``inherit``: the skill has no writes of its own.

This module owns the single decoder and encoder for that declaration, the one
expansion of both temp spellings, the temp-root containment check, and the
session fold that composes loaded skills' scopes by union. It is imported both as
``_write_scope`` by hook subprocesses and as ``autoskillit.hooks._write_scope``
by in-venv callers.
"""

from __future__ import annotations

import os
from collections.abc import Iterable
from enum import StrEnum, unique
from pathlib import Path
from typing import TYPE_CHECKING, NamedTuple, assert_never

# Stdlib-only in subprocess mode: bare-name import. Requires `hooks/_runtime/` on
# sys.path (bootstrap responsibility of the calling script). In-venv callers import
# this module as `autoskillit.hooks._write_scope`, so fall back to a *relative*
# import there — a relative ImportFrom node has no "autoskillit"-prefixed module name
# and so does not trip the stdlib-only AST guard (test_hooks_are_stdlib_only).
if TYPE_CHECKING or __package__:
    from ._runtime import _hook_payload as _hook_payload_module
else:
    import _hook_payload as _hook_payload_module

TEMP_PLACEHOLDER = "{{AUTOSKILLIT_TEMP}}"
WRITE_SCOPE_UNRESTRICTED = "unrestricted"
WRITE_SCOPE_INHERIT = "inherit"


@unique
class WriteScopeKind(StrEnum):
    BOUNDED = "bounded"
    UNRESTRICTED = "unrestricted"
    INHERIT = "inherit"


class WriteScope(NamedTuple):
    """A decoded write-scope declaration; ``paths`` is non-empty iff BOUNDED."""

    kind: WriteScopeKind
    paths: tuple[str, ...] = ()


class WriteScopeError(ValueError):
    """Raised when a write-scope declaration violates the contract."""


def _temp_relative_prefix() -> str:
    return f"{_hook_payload_module.TEMP_RELATIVE_DIR}/"


def _bounded_entry_error(index: int, path: object) -> str | None:
    if not isinstance(path, str) or not path:
        return f"write_paths[{index}] must be a non-empty string"
    if ".." in Path(path).parts:
        return f"write_paths[{index}] must not contain a '..' component (got {path!r})"
    if not path.startswith((f"{TEMP_PLACEHOLDER}/", _temp_relative_prefix())):
        return (
            f"write_paths[{index}] must start with '{TEMP_PLACEHOLDER}/' "
            f"or '{_temp_relative_prefix()}' (got {path!r})"
        )
    return None


def decode_write_scope(raw: object) -> WriteScope:
    """Decode one ``write_paths`` declaration, raising ``WriteScopeError`` on any violation."""
    if isinstance(raw, str):
        if raw == WRITE_SCOPE_UNRESTRICTED:
            return WriteScope(WriteScopeKind.UNRESTRICTED)
        if raw == WRITE_SCOPE_INHERIT:
            return WriteScope(WriteScopeKind.INHERIT)
        raise WriteScopeError(
            f"write_paths string must be {WRITE_SCOPE_UNRESTRICTED!r} or "
            f"{WRITE_SCOPE_INHERIT!r} (got {raw!r})"
        )
    if not isinstance(raw, list):
        raise WriteScopeError(
            "write_paths must be a non-empty list of project temp directories, "
            f"{WRITE_SCOPE_UNRESTRICTED!r}, or {WRITE_SCOPE_INHERIT!r} (got {raw!r})"
        )
    if not raw:
        raise WriteScopeError(
            "an empty write_paths is not a declaration; use `inherit` for a skill "
            "that performs no writes"
        )
    for index, path in enumerate(raw):
        error = _bounded_entry_error(index, path)
        if error is not None:
            raise WriteScopeError(error)
    return WriteScope(WriteScopeKind.BOUNDED, tuple(raw))


def encode_write_scope(scope: WriteScope) -> list[str] | str:
    """Return the frontmatter and manifest encoding that ``decode_write_scope`` accepts."""
    match scope.kind:
        case WriteScopeKind.BOUNDED:
            return list(scope.paths)
        case WriteScopeKind.UNRESTRICTED:
            return WRITE_SCOPE_UNRESTRICTED
        case WriteScopeKind.INHERIT:
            return WRITE_SCOPE_INHERIT
        case _ as unreachable:
            assert_never(unreachable)


def temp_root(project_dir: str) -> str:
    return os.path.join(project_dir, str(_hook_payload_module.TEMP_RELATIVE_DIR))


def expand_write_path(raw: str, project_dir: str) -> str:
    """Lexically expand either temp spelling (or any relative path) under ``project_dir``.

    ``project_dir`` is always the session's working directory: projected skill text
    renders ``{{AUTOSKILLIT_TEMP}}`` as a cwd-relative path, so the model writes there.
    """
    expanded = (
        temp_root(project_dir) + raw[len(TEMP_PLACEHOLDER) :]
        if raw.startswith(TEMP_PLACEHOLDER)
        else raw
    )
    return expanded if os.path.isabs(expanded) else os.path.join(project_dir, expanded)


def _is_within(candidate: str, root: str) -> bool:
    return candidate == root or candidate.startswith(root.rstrip(os.sep) + os.sep)


def temp_root_escape(expanded: str, project_dir: str) -> str | None:
    """Return a cause when ``expanded`` resolves outside the session temp root."""
    real_root = os.path.realpath(temp_root(project_dir))
    real = os.path.realpath(expanded)
    if _is_within(real, real_root):
        return None
    return f"{expanded!r} resolves to {real!r}, outside {real_root!r}"


@unique
class SessionScopeState(StrEnum):
    NONE = "none"
    UNRESTRICTED = "unrestricted"
    BOUNDED = "bounded"


class SessionWriteBoundary(NamedTuple):
    """The union of loaded skills' write scopes.

    ``prefixes`` are the expanded BOUNDED prefixes, deduplicated in first-occurrence
    order; ``contributors`` pairs each BOUNDED skill with its expanded prefixes.
    """

    state: SessionScopeState
    prefixes: tuple[str, ...]
    contributors: tuple[tuple[str, tuple[str, ...]], ...]
    unrestricted_by: tuple[str, ...]


def fold_session_write_scopes(
    scopes: Iterable[tuple[str, WriteScope]], project_dir: str
) -> SessionWriteBoundary:
    """Compose named scopes: BOUNDED unions, UNRESTRICTED dominates, INHERIT abstains."""
    contributors: list[tuple[str, tuple[str, ...]]] = []
    unrestricted_by: list[str] = []
    for name, scope in scopes:
        match scope.kind:
            case WriteScopeKind.BOUNDED:
                contributors.append(
                    (name, tuple(expand_write_path(path, project_dir) for path in scope.paths))
                )
            case WriteScopeKind.UNRESTRICTED:
                unrestricted_by.append(name)
            case WriteScopeKind.INHERIT:
                pass
            case _ as unreachable:
                assert_never(unreachable)
    prefixes = tuple(dict.fromkeys(prefix for _, paths in contributors for prefix in paths))
    if unrestricted_by:
        state = SessionScopeState.UNRESTRICTED
    elif prefixes:
        state = SessionScopeState.BOUNDED
    else:
        state = SessionScopeState.NONE
    return SessionWriteBoundary(state, prefixes, tuple(contributors), tuple(unrestricted_by))


def bounded_scope_contains(scope: WriteScope, path: str, project_dir: str) -> bool:
    """Return whether ``path`` resolves inside one of a BOUNDED scope's directories."""
    if scope.kind is not WriteScopeKind.BOUNDED:
        raise WriteScopeError(f"containment requires a bounded write scope (got {scope.kind})")
    target = os.path.realpath(expand_write_path(path, project_dir))
    return any(
        _is_within(target, os.path.realpath(expand_write_path(declared, project_dir)))
        for declared in scope.paths
    )


__all__ = [
    "TEMP_PLACEHOLDER",
    "WRITE_SCOPE_INHERIT",
    "WRITE_SCOPE_UNRESTRICTED",
    "SessionScopeState",
    "SessionWriteBoundary",
    "WriteScope",
    "WriteScopeError",
    "WriteScopeKind",
    "bounded_scope_contains",
    "decode_write_scope",
    "encode_write_scope",
    "expand_write_path",
    "fold_session_write_scopes",
    "temp_root",
    "temp_root_escape",
]
