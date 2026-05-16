"""Tests for planner-refine SKILL.md cycle-breaking documentation."""

from __future__ import annotations

from pathlib import Path

import pytest

pytestmark = [pytest.mark.layer("skills"), pytest.mark.small]


SKILLS_DIR = Path("src/autoskillit/skills_extended")


class TestPlannerRefineCycleBreaking:
    """Verify planner-refine SKILL.md documents 2-node cycle-breaking correctly."""

    @pytest.fixture
    def skill_md_text(self) -> str:
        skill_md = SKILLS_DIR / "planner-refine" / "SKILL.md"
        if not skill_md.exists():
            pytest.skip("planner-refine SKILL.md not found")
        return skill_md.read_text()

    def test_skill_md_allows_2_node_cycle_breaking(self, skill_md_text) -> None:
        """SKILL.md must allow breaking 2-node mutual cycles."""
        assert "2-node mutual cycle" in skill_md_text or "two-node mutual" in skill_md_text, (
            "SKILL.md must document 2-node mutual cycle-breaking capability"
        )
        assert "higher-numbered WP" in skill_md_text, (
            "SKILL.md must document the higher-numbered WP heuristic for cycle-breaking"
        )

    def test_skill_md_prohibits_3_plus_node_cycle_breaking(self, skill_md_text) -> None:
        """SKILL.md must still escalate 3+ node cycles."""
        assert (
            "cycle_size: 3" in skill_md_text
            or "3+ node" in skill_md_text
            or "three or more" in skill_md_text
        ), "SKILL.md must document escalation of 3+ node cycles"
