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


def parse_int_field(
    data: dict, field_name: str, default: int, source_path: Path, kind: str
) -> int:
    """Parse an integer field from a registry YAML dict, with a descriptive error."""
    val = data.get(field_name, default)
    try:
        return int(val)
    except (ValueError, TypeError) as e:
        name = data.get("name", "?")
        raise TypeError(
            f"{kind} '{name}' field '{field_name}' must be an integer: {source_path}"
        ) from e
