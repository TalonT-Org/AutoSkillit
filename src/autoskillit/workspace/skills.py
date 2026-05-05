"""Skill resolution for bundled skills."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from autoskillit.core import RETIRED_SKILL_NAMES, SkillSource, YAMLError, load_yaml, pkg_root


@dataclass
class SkillInfo:
    name: str
    source: SkillSource
    path: Path
    categories: frozenset[str] = frozenset()


def _read_skill_frontmatter(path: Path) -> dict[str, Any]:
    """Parse SKILL.md YAML frontmatter, returning a dict (empty on any failure)."""
    try:
        with open(path, encoding="utf-8") as fh:
            content = fh.read()
    except (OSError, UnicodeDecodeError):
        return {}
    if not content.startswith("---"):
        return {}
    parts = content.split("---", 2)
    if len(parts) < 3:
        return {}
    try:
        data: Any = load_yaml(parts[1])
    except YAMLError:
        return {}
    return data if isinstance(data, dict) else {}


def read_skill_categories(path: Path) -> frozenset[str]:
    """Parse categories: from SKILL.md YAML frontmatter."""
    data = _read_skill_frontmatter(path)
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


def _skill_info_from_frontmatter(name: str, source: SkillSource, skill_path: Path) -> SkillInfo:
    """Build a SkillInfo by reading all frontmatter fields in a single parse."""
    data = _read_skill_frontmatter(skill_path)
    categories_raw = data.get("categories", [])
    categories = (
        frozenset(str(c) for c in categories_raw)
        if isinstance(categories_raw, list)
        else frozenset()
    )
    return SkillInfo(
        name=name,
        source=source,
        path=skill_path,
        categories=categories,
    )


class DefaultSkillResolver:
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
                return _skill_info_from_frontmatter(name, source, skill_path)
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
    result: list[SkillInfo] = []
    for entry in sorted(directory.iterdir()):
        if not entry.is_dir():
            continue
        if entry.name in RETIRED_SKILL_NAMES:
            raise RuntimeError(
                f"Retired skill name '{entry.name}' found at {entry}. "
                "Remove this directory or remove it from RETIRED_SKILL_NAMES."
            )
        if entry.name in _INTERNAL_SKILLS:
            continue
        skill_path = entry / "SKILL.md"
        if not skill_path.is_file():
            continue
        result.append(_skill_info_from_frontmatter(entry.name, source, skill_path))
    return result
