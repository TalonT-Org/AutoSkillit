"""Structural guards for resolve-review intent-validation phase.

Tests enforce that analysis runs before fixing, parallel sub-agents are used,
ACCEPT/REJECT/DISCUSS classification gates code changes, git history is traced,
raw inline replies are prohibited, and reject patterns are persisted for future mining.
"""

from pathlib import Path

import pytest

pytestmark = [pytest.mark.layer("skills"), pytest.mark.medium]

SKILL_PATH = (
    Path(__file__).parent.parent.parent
    / "src"
    / "autoskillit"
    / "skills_extended"
    / "resolve-review"
    / "SKILL.md"
)
assert SKILL_PATH.exists(), f"SKILL.md not found at {SKILL_PATH}"
SKILL_TEXT = SKILL_PATH.read_text()


# --- Phase ordering ---


def test_analysis_phase_documented_before_fix_phase():
    """The intent-validation / analysis step must appear before the apply-fixes step."""
    text = SKILL_TEXT
    analysis_idx = text.lower().find("intent validation")
    if analysis_idx == -1:
        analysis_idx = text.lower().find("analysis phase")
    if analysis_idx == -1:
        analysis_idx = text.lower().find("accept")
    fix_idx = text.lower().find("apply fix")
    if fix_idx == -1:
        fix_idx = text.lower().find("step 4")
    assert analysis_idx != -1, "SKILL.md must describe an intent-validation or analysis phase"
    assert fix_idx != -1, "SKILL.md must describe a fix application step"
    assert analysis_idx < fix_idx, (
        "Intent-validation (ACCEPT/REJECT/DISCUSS analysis) must appear before the"
        " fix-application step"
    )


def test_analysis_report_written_before_code_changes():
    """The analysis report must be generated BEFORE any code changes."""
    text = SKILL_TEXT
    assert (
        "before any code changes" in text.lower()
        or "before applying" in text.lower()
        or "before code changes" in text.lower()
    ), "SKILL.md must state that the analysis report is written before any code changes are made"


# --- Parallel sub-agents ---


def test_parallel_subagents_per_domain_group():
    """The skill must describe parallel sub-agents grouped by domain/file-area."""
    text = SKILL_TEXT
    assert "domain group" in text.lower() or "file-area" in text.lower(), (
        "SKILL.md must describe grouping comments by domain or file-area"
    )
    assert "parallel" in text.lower() and (
        "sub-agent" in text.lower() or "subagent" in text.lower() or "task" in text.lower()
    ), "SKILL.md must describe launching parallel sub-agents (one per domain group)"


def test_intent_traced_via_git_history():
    """Sub-agents must trace original intent via git history."""
    text = SKILL_TEXT
    assert (
        "git log" in text
        or "git history" in text.lower()
        or "git blame" in text.lower()
        or "pr provenance" in text.lower()
        or "original intent" in text.lower()
    ), (
        "SKILL.md must instruct sub-agents to trace original intent via git history or PR"
        " provenance"
    )


# --- ACCEPT/REJECT/DISCUSS classification ---


def test_accept_reject_discuss_classification_present():
    """The skill must define ACCEPT, REJECT, and DISCUSS classifications."""
    text = SKILL_TEXT
    assert "ACCEPT" in text, "SKILL.md must define the ACCEPT classification"
    assert "REJECT" in text, "SKILL.md must define the REJECT classification"
    assert "DISCUSS" in text, "SKILL.md must define the DISCUSS classification"


def test_only_accept_items_trigger_code_changes():
    """The skill must restrict code changes to ACCEPT items only."""
    text = SKILL_TEXT
    assert (
        "only apply" in text.lower()
        or "accept items only" in text.lower()
        or ("accept" in text.lower() and "only" in text.lower())
    ), "SKILL.md must state that code changes are applied only for ACCEPT items"
    gate_idx = text.lower().find("classification gate")
    if gate_idx == -1:
        gate_idx = text.lower().find("reject/discuss bypass")
    assert gate_idx != -1, "SKILL.md must define a classification gate for REJECT/DISCUSS"
    reject_idx = text.upper().find("REJECT", gate_idx)
    assert reject_idx != -1, "SKILL.md must mention REJECT in the classification gate section"
    reject_context = text[reject_idx : reject_idx + 400].lower()
    assert (
        "no code" in reject_context
        or "excluded" in reject_context
        or ("not" in reject_context and "code" in reject_context)
    ), "SKILL.md must explicitly state that REJECT items do not trigger code changes"


def test_discuss_items_flagged_for_human_decision():
    """DISCUSS items must be flagged for human decision, not fixed automatically."""
    text = SKILL_TEXT
    discuss_idx = text.upper().find("DISCUSS")
    assert discuss_idx != -1, "SKILL.md must mention DISCUSS classification"
    discuss_context = text[discuss_idx : discuss_idx + 400].lower()
    assert (
        "human" in discuss_context or "flag" in discuss_context or "decision" in discuss_context
    ), "SKILL.md must describe DISCUSS items as flagged for human decision"


# --- Publication boundary ---


def test_raw_inline_reply_publication_is_prohibited():
    """Review findings may be published only through the structured tool."""
    text = SKILL_TEXT
    assert "Do Not Post Inline Replies" in text
    assert "/replies" not in text
    assert "post_pr_review" in text


def test_structured_review_tool_is_the_sole_publication_path():
    step65_idx = SKILL_TEXT.find("### Step 6.5")
    step66_idx = SKILL_TEXT.find("### Step 6.6", step65_idx)
    assert step65_idx != -1 and step66_idx != -1
    step65 = SKILL_TEXT[step65_idx:step66_idx]
    assert "sole review-publication path" in step65
    assert "never creates" in step65


# --- Reject pattern persistence ---


def test_reject_patterns_persisted_to_json():
    """REJECT data must be saved to JSON for future reviewer-skill improvement mining."""
    text = SKILL_TEXT
    assert "reject_patterns" in text or "reject patterns" in text.lower(), (
        "SKILL.md must describe saving reject patterns to a JSON file "
        "(for REQ-LOOP-001/002 feedback loop)"
    )
    assert ".json" in text, "SKILL.md must specify a .json file for reject pattern persistence"


# --- Enhanced report ---


def test_report_includes_all_three_classification_counts():
    """The final report must show ACCEPT, REJECT, and DISCUSS statistics."""
    text = SKILL_TEXT
    report_idx = text.find("### Step 7")
    assert report_idx != -1, "SKILL.md must have a Step 7 (Report)"
    report_section = text[report_idx:]
    assert "ACCEPT" in report_section or "accept" in report_section.lower(), (
        "Report section must include ACCEPT count"
    )
    assert "REJECT" in report_section or "reject" in report_section.lower(), (
        "Report section must include REJECT count"
    )
    assert "DISCUSS" in report_section or "discuss" in report_section.lower(), (
        "Report section must include DISCUSS count"
    )
