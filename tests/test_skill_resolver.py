"""Tests for skill resolution hierarchy."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from automation_mcp.config import AutomationConfig, SkillsConfig
from automation_mcp.skill_resolver import SkillResolver, bundled_skills_dir

BUNDLED_SKILLS = [
    "assess-and-merge",
    "dry-walkthrough",
    "implement-worktree",
    "implement-worktree-no-merge",
    "investigate",
    "make-plan",
    "mermaid",
    "rectify",
    "retry-worktree",
    "review-approach",
]


def _create_skill(base: Path, name: str) -> Path:
    """Create a minimal SKILL.md in base/name/."""
    d = base / name
    d.mkdir(parents=True, exist_ok=True)
    md = d / "SKILL.md"
    md.write_text(f"---\nname: {name}\n---\n")
    return md


class TestSkillResolver:
    # SK1
    def test_bundled_skill_found(self, tmp_path: Path) -> None:
        project = tmp_path / "project"
        project.mkdir()
        config = AutomationConfig()
        resolver = SkillResolver(project, config)
        info = resolver.resolve("investigate")
        assert info is not None
        assert info.name == "investigate"
        assert info.source == "bundled"
        assert info.path.name == "SKILL.md"

    # SK2
    def test_project_overrides_bundled(self, tmp_path: Path) -> None:
        project = tmp_path / "project"
        project_skills = project / ".claude" / "skills"
        _create_skill(project_skills, "investigate")

        config = AutomationConfig()
        resolver = SkillResolver(project, config)
        info = resolver.resolve("investigate")
        assert info is not None
        assert info.source == "project"
        assert info.path == project_skills / "investigate" / "SKILL.md"

    # SK3
    def test_user_overrides_bundled(self, tmp_path: Path) -> None:
        project = tmp_path / "project"
        project.mkdir()
        user_home = tmp_path / "home"
        user_skills = user_home / ".claude" / "skills"
        _create_skill(user_skills, "investigate")

        config = AutomationConfig()
        with patch("automation_mcp.skill_resolver.Path.home", return_value=user_home):
            resolver = SkillResolver(project, config)
            info = resolver.resolve("investigate")
        assert info is not None
        assert info.source == "user"

    # SK4
    def test_project_overrides_user(self, tmp_path: Path) -> None:
        project = tmp_path / "project"
        project_skills = project / ".claude" / "skills"
        _create_skill(project_skills, "investigate")

        user_home = tmp_path / "home"
        user_skills = user_home / ".claude" / "skills"
        _create_skill(user_skills, "investigate")

        config = AutomationConfig()
        with patch("automation_mcp.skill_resolver.Path.home", return_value=user_home):
            resolver = SkillResolver(project, config)
            info = resolver.resolve("investigate")
        assert info is not None
        assert info.source == "project"

    # SK5
    def test_list_all_shows_sources(self, tmp_path: Path) -> None:
        project = tmp_path / "project"
        project_skills = project / ".claude" / "skills"
        _create_skill(project_skills, "investigate")
        _create_skill(project_skills, "custom-skill")

        config = AutomationConfig()
        resolver = SkillResolver(project, config)
        skills = resolver.list_all()

        names = {s.name for s in skills}
        assert "investigate" in names
        assert "custom-skill" in names

        sources = {s.name: s.source for s in skills}
        assert sources["investigate"] == "project"
        assert sources["custom-skill"] == "project"
        assert sources["mermaid"] == "bundled"

    # SK6
    def test_unknown_skill_returns_none(self, tmp_path: Path) -> None:
        project = tmp_path / "project"
        project.mkdir()
        config = AutomationConfig()
        resolver = SkillResolver(project, config)
        assert resolver.resolve("nonexistent") is None

    # SK7
    def test_scan_finds_skill_md(self, tmp_path: Path) -> None:
        project = tmp_path / "project"
        skill_dir = project / ".claude" / "skills"
        _create_skill(skill_dir, "alpha")
        _create_skill(skill_dir, "beta")
        # Directory without SKILL.md should be ignored
        (skill_dir / "empty").mkdir()

        config = AutomationConfig()
        resolver = SkillResolver(project, config)
        names = {s.name for s in resolver.list_all() if s.source == "project"}
        assert "alpha" in names
        assert "beta" in names
        assert "empty" not in names

    # SK8
    def test_bundled_skills_all_present(self) -> None:
        bd = bundled_skills_dir()
        for name in BUNDLED_SKILLS:
            skill_md = bd / name / "SKILL.md"
            assert skill_md.is_file(), f"Missing bundled skill: {name}"

    # SK9
    def test_resolution_order_configurable(self, tmp_path: Path) -> None:
        project = tmp_path / "project"
        project_skills = project / ".claude" / "skills"
        _create_skill(project_skills, "investigate")

        config = AutomationConfig(skills=SkillsConfig(resolution_order=["bundled", "project"]))
        resolver = SkillResolver(project, config)
        info = resolver.resolve("investigate")
        assert info is not None
        assert info.source == "bundled"

    def test_empty_resolution_order(self, tmp_path: Path) -> None:
        project = tmp_path / "project"
        project.mkdir()
        config = AutomationConfig(skills=SkillsConfig(resolution_order=[]))
        resolver = SkillResolver(project, config)
        assert resolver.resolve("investigate") is None
        assert resolver.list_all() == []
