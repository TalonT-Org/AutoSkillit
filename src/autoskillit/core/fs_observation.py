"""Shared funnel for observing filesystem paths obtained by enumeration.

A path produced by ``os.walk``, ``glob``, ``rglob``, ``scandir``, or
``listdir`` is a claim about the past, not a fact about the present — the
entry may vanish between enumeration and the moment it is stat'd. Every
caller that walks a directory and then stats what it found must route the
observation through this module rather than calling ``.stat()``/``.lstat()``/
``os.path.getmtime()`` directly, so a concurrent deletion produces a defined
``None`` result instead of an uncaught ``OSError`` far up the call stack.

Stdlib-only: importable from hook subprocesses and every layer above IL-0.
"""

from __future__ import annotations

import os
from pathlib import Path

__all__ = ["observe_path_mode", "safe_mtime"]

# Both mean the path stopped resolving between enumeration and observation:
# FileNotFoundError when the leaf entry itself is gone, NotADirectoryError
# when an intermediate directory component was replaced by a regular file
# mid-walk (verified: lstat/stat/getmtime all raise NotADirectoryError, never
# FileNotFoundError, for that shape). Every other OSError — PermissionError
# in particular — propagates: a permissions or IO fault is a real failure and
# must not be laundered into "vanished".
_VANISHED_ERRORS = (FileNotFoundError, NotADirectoryError)


def observe_path_mode(path: Path) -> int | None:
    """Return ``st_mode`` from ``lstat``, or ``None`` when the path no longer resolves."""
    try:
        return path.lstat().st_mode
    except _VANISHED_ERRORS:
        return None


def safe_mtime(path: Path) -> float | None:
    """Return ``st_mtime``, or ``None`` when the path no longer resolves."""
    try:
        return os.path.getmtime(path)
    except _VANISHED_ERRORS:
        return None
