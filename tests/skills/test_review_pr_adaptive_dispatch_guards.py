"""Behavioral guard tests for review-pr adaptive subagent dispatch."""

from __future__ import annotations

from pathlib import Path

SKILL_PATH = (
    Path(__file__).parent.parent.parent
    / "src"
    / "autoskillit"
    / "skills_extended"
    / "review-pr"
    / "SKILL.md"
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


def test_step3_requires_single_message_dispatch():
    """Step 3 must contain explicit single-message parallel dispatch instruction."""
    import re

    text = _skill_text()
    step_blocks = re.split(r"(?m)^#{1,3}\s+Step\s+\d+", text)
    step3_blocks = [
        b
        for b in step_blocks
        if "DISPATCH_AGENTS" in b and ("spawn" in b.lower() or "task tool" in b.lower())
    ]
    assert step3_blocks, "Could not locate Step 3 (dispatch step) in review-pr SKILL.md"
    assert any("single message" in b.lower() for b in step3_blocks), (
        "review-pr/SKILL.md Step 3 must contain 'single message' dispatch "
        "instruction to prevent sequential subagent dispatch"
    )
