"""Behavioral guard tests for review-pr/SKILL.md prior-thread awareness (T_RPA1–T_RPA7).

These tests verify that the review-pr skill correctly documents and implements
prior review thread suppression and awareness introduced by the check_review_loop
feature.
"""

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


# T_RPA1
def test_skill_contains_step_1_5_section() -> None:
    """SKILL.md must contain a 'Step 1.5' section for prior thread fetch."""
    text = _skill_text()
    assert "Step 1.5" in text, (
        "review-pr/SKILL.md must contain a 'Step 1.5' section that fetches prior "
        "review thread context before running parallel audit subagents."
    )


# T_RPA2
def test_step_1_5_references_graphql_review_threads() -> None:
    """Step 1.5 must reference 'gh api graphql' and 'reviewThreads' for fetching threads."""
    text = _skill_text()
    assert "reviewThreads" in text, (
        "review-pr/SKILL.md Step 1.5 must reference 'reviewThreads' in the GraphQL "
        "query used to fetch prior review thread context."
    )
    # Step 1.5 should reference graphql (the query is embedded inline)
    assert "graphql" in text.lower(), (
        "review-pr/SKILL.md Step 1.5 must use a GraphQL query to fetch review threads."
    )


# T_RPA3
def test_step_1_5_distinguishes_resolved_from_unresolved() -> None:
    """Step 1.5 must distinguish isResolved=true (prior_resolved) from isResolved=false (prior_unresolved)."""
    text = _skill_text()
    assert "prior_resolved_findings" in text, (
        "review-pr/SKILL.md Step 1.5 must build a 'prior_resolved_findings' list for "
        "threads where isResolved=true and body contains [critical] or [warning]."
    )
    assert "prior_unresolved_findings" in text, (
        "review-pr/SKILL.md Step 1.5 must build a 'prior_unresolved_findings' list for "
        "threads where isResolved=false and body contains [critical] or [warning]."
    )


# T_RPA4
def test_step_3_includes_do_not_reraise_instruction_for_resolved() -> None:
    """Step 3 subagent prompt must include 'DO NOT RE-RAISE' for prior resolved findings."""
    text = _skill_text()
    assert "DO NOT RE-RAISE" in text, (
        "review-pr/SKILL.md Step 3 subagent prompt must instruct subagents to not "
        "re-raise prior resolved findings (DO NOT RE-RAISE instruction)."
    )


# T_RPA5
def test_step_3_includes_focus_on_instruction_for_unresolved() -> None:
    """Step 3 subagent prompt must include 'FOCUS ON' for prior unresolved findings."""
    text = _skill_text()
    assert "FOCUS ON" in text, (
        "review-pr/SKILL.md Step 3 subagent prompt must instruct subagents to focus "
        "on prior unresolved findings (FOCUS ON instruction)."
    )


# T_RPA6
def test_step_4_mentions_filtering_suppressing_resolved_findings() -> None:
    """Step 4 aggregation must mention filtering or suppressing prior_resolved_findings."""
    text = _skill_text()
    lower = text.lower()
    has_suppress = "suppress" in lower or "filter" in lower or "suppression" in lower
    assert has_suppress, (
        "review-pr/SKILL.md Step 4 aggregation must mention filtering or suppressing "
        "findings that match prior_resolved_findings to avoid re-raising fixed issues."
    )
    # Must also reference the prior_resolved_findings in the Step 4 context
    assert "prior_resolved" in text, (
        "review-pr/SKILL.md Step 4 must reference 'prior_resolved' entries when "
        "describing the suppression pass."
    )


# T_RPA7
def test_prior_resolved_matched_by_file_and_line_proximity() -> None:
    """prior_resolved_findings must be matched by (file, line) proximity (±N lines), not exact equality."""
    text = _skill_text()
    has_proximity = "±" in text or "+/-" in text or "within" in text.lower()
    assert has_proximity, (
        "review-pr/SKILL.md must specify that prior_resolved_findings matches use "
        "line proximity (±N lines) to handle line drift from fix commits — not exact "
        "line equality."
    )
