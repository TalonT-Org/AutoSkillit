"""Backend-neutral shape contract for managed skill catalog entries."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

MANAGED_SKILL_FILENAME = "SKILL.md"


def validate_managed_skill_entries(
    entries: Sequence[tuple[str, str]],
) -> dict[str, Path]:
    """Return canonical relative paths after validating managed entry identity."""
    validated: dict[str, Path] = {}
    for name, relative_path in entries:
        if not name or Path(name).name != name or name.startswith("."):
            raise ValueError(f"invalid managed skill name: {name!r}")
        if name in validated:
            raise ValueError(f"duplicate managed skill name: {name}")
        expected_path = Path(name) / MANAGED_SKILL_FILENAME
        if Path(relative_path) != expected_path:
            raise ValueError(f"managed skill entry {name!r} must use {expected_path.as_posix()!r}")
        validated[name] = expected_path
    return validated


__all__ = ["MANAGED_SKILL_FILENAME", "validate_managed_skill_entries"]
