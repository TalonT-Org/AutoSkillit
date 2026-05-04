"""Behavioral guard tests for review-pr adaptive subagent dispatch."""

from __future__ import annotations

from pathlib import Path

SKILL_PATH = (
    Path(__file__).parent.parent.parent
    / "src" / "autoskillit" / "skills_extended" / "review-pr" / "SKILL.md"
)


def _skill_text() -> str:
    return SKILL_PATH.read_text()


def test_skill_accepts_diff_metrics_path_argument():
    text = _skill_text()
    assert "diff_metrics_path" in text


def test_skill_defines_diff_size_gate_step():
    text = _skill_text()
    assert "dispatch_agents" in text


def test_small_diff_skips_defense_bugs_slop():
    text = _skill_text().lower()
    assert "small" in text


def test_small_diff_always_includes_tests_cohesion():
    text = _skill_text().lower()
    assert "tests" in text
    assert "cohesion" in text


def test_full_fanout_for_medium_and_large():
    text = _skill_text()
    for agent in ["arch", "tests", "defense", "bugs", "cohesion", "slop"]:
        assert agent in text
