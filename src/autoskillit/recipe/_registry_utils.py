"""Shared utilities for recipe registry loaders."""

from __future__ import annotations

from pathlib import Path

_MISSING_MTIME: float = -1.0


def dir_mtime(path: Path) -> float:
    """Return directory mtime, or ``_MISSING_MTIME`` if the path is inaccessible."""
    try:
        return path.stat().st_mtime
    except OSError:
        return _MISSING_MTIME
