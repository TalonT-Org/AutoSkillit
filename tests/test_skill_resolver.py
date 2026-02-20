"""Tests for skill resolution hierarchy."""

from __future__ import annotations

import re
from pathlib import Path

from autoskillit.skill_resolver import SkillResolver, bundled_skills_dir

BUNDLED_SKILLS = [
    "assess-and-merge",
    "bugfix-loop",
    "dry-walkthrough",
    "implement-worktree",
    "implement-worktree-no-merge",
    "implementation-pipeline",
    "investigate",
    "investigate-first",
    "make-plan",
    "make-script-skill",
    "mermaid",
    "rectify",
    "retry-worktree",
    "review-approach",
    "setup-project",
]

BUNDLED_SKILL_NAMES = set(BUNDLED_SKILLS)


class TestSkillResolver:
    # SK1
    def test_bundled_skill_found(self) -> None:
        resolver = SkillResolver()
        info = resolver.resolve("investigate")
        assert info is not None
        assert info.name == "investigate"
        assert info.source == "bundled"
        assert info.path.name == "SKILL.md"

    # SK6
    def test_unknown_skill_returns_none(self) -> None:
        resolver = SkillResolver()
        assert resolver.resolve("nonexistent") is None

    # SK8
    def test_bundled_skills_match_filesystem(self) -> None:
        """BUNDLED_SKILLS list must exactly match what's on the filesystem."""
        bd = bundled_skills_dir()
        actual = sorted(d.name for d in bd.iterdir() if d.is_dir() and (d / "SKILL.md").is_file())
        assert actual == sorted(BUNDLED_SKILLS), (
            f"BUNDLED_SKILLS out of sync.\n"
            f"  On disk: {actual}\n"
            f"  In test: {sorted(BUNDLED_SKILLS)}"
        )

    def test_list_all_returns_bundled_skills(self) -> None:
        """list_all returns all bundled skills with source='bundled'."""
        resolver = SkillResolver()
        skills = resolver.list_all()
        names = {s.name for s in skills}
        assert "investigate" in names
        assert "make-plan" in names
        sources = {s.source for s in skills}
        assert sources == {"bundled"}

    def test_skill_md_cross_references_are_namespaced(self) -> None:
        """All /skill-name references in SKILL.md files use /autoskillit: prefix."""
        import autoskillit

        skills_dir = Path(autoskillit.__file__).parent / "skills"
        for skill_md in skills_dir.rglob("SKILL.md"):
            content = skill_md.read_text()
            for match in re.finditer(r"(?<!\w)/([a-z][\w-]+)", content):
                name = match.group(1)
                if name.startswith("autoskillit:") or name.startswith("mcp__"):
                    continue
                # Skip URI paths like workflow://bugfix-loop — not skill invocations
                start = match.start()
                if start >= 1 and content[start - 1] == "/":
                    continue
                if name in BUNDLED_SKILL_NAMES:
                    assert False, (
                        f"{skill_md.parent.name}/SKILL.md: /{name} should be /autoskillit:{name}"
                    )
