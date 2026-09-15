"""Canonical skill-entry identity: skill name to catalog-relative path.

Kept import-free of sibling ``_type_*`` modules (only ``pathlib``) so it can
be imported by any other ``core/types`` module — including ``_type_backend``
and ``_type_plugin_source`` — without risking an import cycle.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

__all__ = [
    "MANAGED_SKILL_FILENAME",
    "skill_relative_path",
    "validate_skill_entries",
]

MANAGED_SKILL_FILENAME = "SKILL.md"


def skill_relative_path(name: str) -> Path:
    """Return the canonical relative path for one skill catalog entry."""
    return Path(name) / MANAGED_SKILL_FILENAME


def validate_skill_entries(
    entries: Sequence[tuple[str, str]],
) -> dict[str, Path]:
    """Return canonical relative paths after validating skill entry identity.

    Shared by managed and projected skill catalogs: both use the same
    (name, relative_path) identity contract.
    """
    validated: dict[str, Path] = {}
    for name, relative_path in entries:
        if not name or Path(name).name != name or name.startswith("."):
            raise ValueError(f"invalid skill name: {name!r}")
        if name in validated:
            raise ValueError(f"duplicate skill name: {name}")
        expected_path = skill_relative_path(name)
        if Path(relative_path) != expected_path:
            raise ValueError(f"skill entry {name!r} must use {expected_path.as_posix()!r}")
        validated[name] = expected_path
    return validated
