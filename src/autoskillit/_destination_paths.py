"""Stdlib-only write-destination identity shared by hooks and in-process callers."""

from __future__ import annotations

import os
from pathlib import Path


def destination_location(destination: str | os.PathLike[str]) -> Path:
    """Resolve the parent of a write destination, preserving its final symlink entry."""
    path = Path(destination)
    if not path.name or path.name == "..":
        return path.resolve()
    return path.parent.resolve() / path.name
