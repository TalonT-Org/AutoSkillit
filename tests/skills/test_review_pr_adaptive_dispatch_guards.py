"""Behavioral guard tests for review-pr adaptive subagent dispatch."""

from __future__ import annotations

from pathlib import Path

import pytest

pytestmark = [pytest.mark.layer("skills"), pytest.mark.medium]

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


def _section(start_heading: str, end_heading: str) -> str:
    text = _skill_text()
    start = text.index(start_heading)
    end = text.index(end_heading, start)
    return text[start:end]


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


def test_gate_validation_precedes_boolean_consumption() -> None:
    section = _section("### Step 2.7", "### Step 2.5")
    assert section.index("METRICS_MARKER_BEFORE") < section.index("run_overengineering_audits")
    assert section.index("artifact_digest_mismatch") < section.index("GATE_STATE=valid_true")
    assert 'type == "boolean"' in section


def test_standard_and_experimental_dispatch_are_separate() -> None:
    section = _section("### Step 2.9", "### Step 3")
    assert "STANDARD_DISPATCH_AGENTS" in section
    assert "EXPERIMENTAL_DISPATCH_AGENTS" in section
    assert "STANDARD_AGENT_ALLOWLIST" in section
    assert "EXPERIMENTAL_AGENT_ALLOWLIST" in section
    assert "intersection" in section.lower()
    assert "deletion_context" in section


def test_true_gate_dispatches_both_registered_agents_once() -> None:
    section = _section("### Step 3", "### Step 4")
    for name in (
        "autoskillit:pr-review-auditor-reachability",
        "autoskillit:pr-review-auditor-abstraction-surface",
    ):
        assert section.count(f'Agent(subagent_type="{name}", model="sonnet")') == 1
    assert "ANNOTATED_DIFF" in section
    assert "VALID_DIFF_LINES" in section
    assert "fixed configured agent order" in section


def test_standard_fallback_never_contains_experimental_agents() -> None:
    section = _section("### Step 2.9", "### Step 3")
    fallback = section[section.index("all six standard agents") :]
    assert "pr-review-auditor-reachability" not in fallback
    assert "pr-review-auditor-abstraction-surface" not in fallback
