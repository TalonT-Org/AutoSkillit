"""Skill resolution for bundled skills."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from autoskillit.types import SkillSource


@dataclass
class SkillInfo:
    name: str
    source: SkillSource
    path: Path


class SkillResolver:
    """List bundled skills from the package directory."""

    def __init__(self) -> None:
        self._dir = bundled_skills_dir()

    def resolve(self, name: str) -> SkillInfo | None:
        """Resolve a skill name to its path."""
        skill_path = self._dir / name / "SKILL.md"
        if skill_path.is_file():
            return SkillInfo(name=name, source=SkillSource.BUNDLED, path=skill_path)
        return None

    def list_all(self) -> list[SkillInfo]:
        """List all bundled skills."""
        return sorted(
            _scan_directory(SkillSource.BUNDLED, self._dir), key=lambda s: s.name
        )


def bundled_skills_dir() -> Path:
    """Return the path to the bundled skills directory."""
    return Path(__file__).parent / "skills"


def _scan_directory(source: SkillSource, directory: Path) -> list[SkillInfo]:
    """Find all SKILL.md files in immediate subdirectories."""
    if not directory.is_dir():
        return []
    return [
        SkillInfo(name=d.name, source=source, path=d / "SKILL.md")
        for d in sorted(directory.iterdir())
        if d.is_dir() and (d / "SKILL.md").is_file()
    ]
