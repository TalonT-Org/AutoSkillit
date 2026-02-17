"""Skill resolution with project > user > bundled hierarchy."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from automation_mcp.config import AutomationConfig


@dataclass
class SkillInfo:
    name: str
    source: str  # "project" | "user" | "bundled"
    path: Path


def build_skill_roots(project_dir: Path, config: AutomationConfig) -> list[tuple[str, Path]]:
    """Build ordered list of (source, directory) tuples for skill resolution.

    Used by both SkillResolver (CLI) and SkillsDirectoryProvider (MCP).
    """
    source_map = {
        "project": project_dir / ".claude" / "skills",
        "user": Path.home() / ".claude" / "skills",
        "bundled": Path(__file__).parent / "skills",
    }
    return [
        (source, source_map[source])
        for source in config.skills.resolution_order
        if source in source_map
    ]


class SkillResolver:
    """Resolve skill names to SKILL.md paths using a configurable hierarchy."""

    def __init__(self, project_dir: Path, config: AutomationConfig):
        self._dirs = build_skill_roots(project_dir, config)

    def resolve(self, name: str) -> SkillInfo | None:
        """Resolve a skill name to its path. First match in hierarchy wins."""
        for source, directory in self._dirs:
            skill_path = directory / name / "SKILL.md"
            if skill_path.is_file():
                return SkillInfo(name=name, source=source, path=skill_path)
        return None

    def list_all(self) -> list[SkillInfo]:
        """List all available skills. Higher-priority sources shadow lower ones."""
        seen: set[str] = set()
        skills: list[SkillInfo] = []
        for source, directory in self._dirs:
            for info in _scan_directory(source, directory):
                if info.name not in seen:
                    seen.add(info.name)
                    skills.append(info)
        return sorted(skills, key=lambda s: s.name)


def bundled_skills_dir() -> Path:
    """Return the path to the bundled skills directory."""
    return Path(__file__).parent / "skills"


def _scan_directory(source: str, directory: Path) -> list[SkillInfo]:
    """Find all SKILL.md files in immediate subdirectories."""
    if not directory.is_dir():
        return []
    return [
        SkillInfo(name=d.name, source=source, path=d / "SKILL.md")
        for d in sorted(directory.iterdir())
        if d.is_dir() and (d / "SKILL.md").is_file()
    ]
