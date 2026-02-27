"""Migration note discovery and version chaining.

Discovers versioned migration YAML files from the bundled ``migrations/``
directory and determines which notes apply to a given script version.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from packaging.version import Version

from autoskillit.core.io import load_yaml


@dataclass
class MigrationChange:
    """A single change described in a migration note."""

    id: str
    description: str
    instruction: str
    detect: dict[str, str] = field(default_factory=dict)
    example_before: str = ""
    example_after: str = ""


@dataclass
class MigrationNote:
    """A versioned migration note describing changes between two versions."""

    from_version: str
    to_version: str
    description: str
    changes: list[MigrationChange]
    path: Path


def _migrations_dir() -> Path:
    """Return path to bundled migrations directory."""
    return Path(__file__).parent / "migrations"


def list_migrations() -> list[MigrationNote]:
    """Discover and parse all migration YAML files, sorted by filename."""
    mig_dir = _migrations_dir()
    if not mig_dir.is_dir():
        return []

    notes: list[MigrationNote] = []
    for f in sorted(mig_dir.iterdir()):
        if f.suffix in (".yaml", ".yml") and f.is_file():
            note = _parse_migration(f)
            notes.append(note)
    return notes


def applicable_migrations(
    script_version: str | None, installed_version: str
) -> list[MigrationNote]:
    """Return migration notes applicable to a script at the given version.

    Chains migrations from *script_version* up to *installed_version*.

    Algorithm:
    1. current = Version(script_version) or Version("0.0.0") if None
    2. Sort migrations by to_version ascending
    3. For each migration: if from_version <= current < to_version, include it
       and advance current = to_version
    4. Stop when current >= installed_version or no more migrations
    """
    current = Version(script_version) if script_version else Version("0.0.0")
    target = Version(installed_version)

    if current >= target:
        return []

    all_notes = list_migrations()
    all_notes.sort(key=lambda n: Version(n.to_version))

    result: list[MigrationNote] = []
    for note in all_notes:
        fv = Version(note.from_version)
        tv = Version(note.to_version)
        if fv <= current < tv:
            result.append(note)
            current = tv
        if current >= target:
            break

    return result


def _parse_migration(path: Path) -> MigrationNote:
    """Parse a migration YAML file into a MigrationNote."""
    data = load_yaml(path)
    if not isinstance(data, dict):
        raise ValueError(f"Migration file must contain a YAML mapping: {path}")

    from_version = data.get("from_version")
    to_version = data.get("to_version")
    description = data.get("description", "")

    if not from_version or not to_version:
        raise ValueError(f"Migration must have 'from_version' and 'to_version': {path}")

    changes: list[MigrationChange] = []
    for change_data in data.get("changes", []):
        if not isinstance(change_data, dict):
            continue
        change_id = change_data.get("id")
        if not change_id:
            raise ValueError(f"Migration change must have 'id': {path}")
        changes.append(
            MigrationChange(
                id=change_id,
                description=change_data.get("description", ""),
                instruction=change_data.get("instruction", ""),
                detect=change_data.get("detect", {}),
                example_before=change_data.get("example_before", ""),
                example_after=change_data.get("example_after", ""),
            )
        )

    return MigrationNote(
        from_version=from_version,
        to_version=to_version,
        description=description,
        changes=changes,
        path=path,
    )
