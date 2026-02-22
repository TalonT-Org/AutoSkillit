"""Tests for skill resolution hierarchy."""

from __future__ import annotations

import re
from pathlib import Path

from autoskillit.skill_resolver import SkillResolver, bundled_skills_dir
from autoskillit.types import SkillSource

BUNDLED_SKILLS = [
    "assess-and-merge",
    "dry-walkthrough",
    "implement-worktree",
    "implement-worktree-no-merge",
    "investigate",
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
        assert info.source == SkillSource.BUNDLED
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
        assert sources == {SkillSource.BUNDLED}

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

    def test_skill_md_yaml_examples_are_valid_workflows(self) -> None:
        """YAML workflow examples embedded in SKILL.md files must pass validation."""
        import yaml

        import autoskillit
        from autoskillit.workflow_loader import _parse_workflow, validate_workflow

        skills_dir = Path(autoskillit.__file__).parent / "skills"
        yaml_block_re = re.compile(r"```yaml\n(.*?)```", re.DOTALL)

        for skill_md in skills_dir.rglob("SKILL.md"):
            content = skill_md.read_text()
            for match in yaml_block_re.finditer(content):
                block = match.group(1)
                # Only validate blocks that look like full workflow definitions
                if "steps:" not in block or "name:" not in block:
                    continue
                # Skip format templates that use {placeholder} syntax
                if "{script-name}" in block or "{mcp_tool_name}" in block:
                    continue
                data = yaml.safe_load(block)
                if not isinstance(data, dict) or "steps" not in data:
                    continue
                wf = _parse_workflow(data)
                errors = validate_workflow(wf)
                assert not errors, (
                    f"{skill_md.parent.name}/SKILL.md has invalid YAML example:\n  {errors}"
                )
