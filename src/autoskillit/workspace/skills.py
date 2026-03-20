"""Skill resolution for bundled skills."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from autoskillit.core import SkillSource, YAMLError, load_yaml, pkg_root


@dataclass
class SkillInfo:
    name: str
    source: SkillSource
    path: Path
    categories: frozenset[str] = field(default_factory=frozenset)


def read_skill_categories(path: Path) -> frozenset[str]:
    """Parse categories: from SKILL.md YAML frontmatter."""
    try:
        with open(path, encoding="utf-8") as fh:
            content = fh.read()
    except (OSError, UnicodeDecodeError):
        return frozenset()
    if not content.startswith("---"):
        return frozenset()
    parts = content.split("---", 2)
    if len(parts) < 3:
        return frozenset()
    try:
        data: Any = load_yaml(parts[1])
    except YAMLError:
        return frozenset()
    if not isinstance(data, dict):
        return frozenset()
    categories = data.get("categories", [])
    if not isinstance(categories, list):
        return frozenset()
    return frozenset(str(c) for c in categories)


_INTERNAL_SKILLS: frozenset[str] = frozenset({"sous-chef"})

_OVERRIDE_SEARCH_DIRS: tuple[str, ...] = (".claude/skills", ".autoskillit/skills")


def detect_project_local_overrides(project_dir: Path) -> frozenset[str]:
    """Return the set of bundled skill names overridden by project-local SKILL.md files.

    Scans .claude/skills/<name>/SKILL.md and .autoskillit/skills/<name>/SKILL.md
    under project_dir. Returns a frozenset of skill names that have a project-local
    override. Returns an empty frozenset if project_dir does not exist.
    """
    overrides: set[str] = set()
    for subdir in _OVERRIDE_SEARCH_DIRS:
        search_root = project_dir / subdir
        if not search_root.is_dir():
            continue
        try:
            entries = list(search_root.iterdir())
        except OSError:
            continue
        for entry in entries:
            if entry.is_dir() and (entry / "SKILL.md").is_file():
                overrides.add(entry.name)
    return frozenset(overrides)


class SkillResolver:
    """List bundled skills from both the skills/ and skills_extended/ directories."""

    def __init__(self) -> None:
        self._dir = bundled_skills_dir()
        self._extended_dir = bundled_skills_extended_dir()

    def resolve(self, name: str) -> SkillInfo | None:
        """Resolve a skill name to its path. Checks skills/ before skills_extended/."""
        for directory, source in (
            (self._dir, SkillSource.BUNDLED),
            (self._extended_dir, SkillSource.BUNDLED_EXTENDED),
        ):
            skill_path = directory / name / "SKILL.md"
            if skill_path.is_file():
                return SkillInfo(
                    name=name,
                    source=source,
                    path=skill_path,
                    categories=read_skill_categories(skill_path),
                )
        return None

    def list_all(self) -> list[SkillInfo]:
        """List all public bundled skills from both directories."""
        bundled = _scan_directory(SkillSource.BUNDLED, self._dir)
        extended = _scan_directory(SkillSource.BUNDLED_EXTENDED, self._extended_dir)
        combined = sorted(bundled + extended, key=lambda s: s.name)
        # Structural guard: no name may appear in both directories.
        names = [s.name for s in combined]
        dupes = {n for n in names if names.count(n) > 1}
        if dupes:
            raise RuntimeError(
                f"Skill name collision across skills/ and skills_extended/: {sorted(dupes)}"
            )
        return combined


def bundled_skills_dir() -> Path:
    """Return the path to the bundled skills directory."""
    return pkg_root() / "skills"


def bundled_skills_extended_dir() -> Path:
    """Return the path to the extended bundled skills directory (Tier 2+3)."""
    return pkg_root() / "skills_extended"


def _scan_directory(source: SkillSource, directory: Path) -> list[SkillInfo]:
    """Find all SKILL.md files in immediate subdirectories."""
    if not directory.is_dir():
        return []
    return [
        SkillInfo(
            name=d.name,
            source=source,
            path=d / "SKILL.md",
            categories=read_skill_categories(d / "SKILL.md"),
        )
        for d in sorted(directory.iterdir())
        if d.is_dir() and (d / "SKILL.md").is_file() and d.name not in _INTERNAL_SKILLS
    ]
