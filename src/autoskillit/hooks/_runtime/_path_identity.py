"""Symmetric path identity for hook processes.

Compares canonical identities of the filesystem as it is at call time; it does
not stop a later symlink swap, since the stdlib has no
``openat2(RESOLVE_BENEATH)``.

Stdlib-only; runs as a bare sibling module under ``hooks/_runtime/``.
"""

from __future__ import annotations

import os
from pathlib import Path


class PathIdentityError(ValueError):
    """A path has no canonical identity, or lies outside the expected tree."""


def canonical_existing_path(path: str | os.PathLike[str]) -> Path:
    """Return the symlink-free identity of an existing path."""
    try:
        return Path(path).resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise PathIdentityError(f"no canonical identity for {os.fspath(path)!r}: {exc}") from exc


def hooks_relative_key(script_identity: str | os.PathLike[str], hooks_dir: Path) -> str:
    """Return the hooks-relative POSIX key of a script, canonicalizing both operands."""
    script = canonical_existing_path(script_identity)
    root = canonical_existing_path(hooks_dir)
    try:
        return script.relative_to(root).as_posix()
    except ValueError as exc:
        raise PathIdentityError(
            f"script {str(script)!r} is outside the hooks tree {str(root)!r}"
        ) from exc


def realpath_within(candidate: str | os.PathLike[str], root: str | os.PathLike[str]) -> bool:
    """Return whether *candidate* lies at or under *root* after resolving both."""
    try:
        canonical_root = os.path.realpath(root)
        return os.path.commonpath((os.path.realpath(candidate), canonical_root)) == canonical_root
    except (OSError, ValueError):
        return False
