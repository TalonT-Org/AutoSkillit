"""Structural guards for review-pr/SKILL.md posting mechanics.

Tests enforce orchestrator behavioral guardrails (echo-the-rule,
post-confirmation, degraded labeling), subagent [LNNN] marker guidance,
and a non-table tiered fallback. Each test makes it impossible to
silently regress its guarded element.
"""

import re
from pathlib import Path

SKILL_PATH = (
    Path(__file__).parent.parent.parent
    / "src"
    / "autoskillit"
    / "skills"
    / "review-pr"
    / "SKILL.md"
)


SKILL_TEXT = SKILL_PATH.read_text()


# --- Orchestrator behavioral guardrails ---


def test_skill_has_pre_posting_echo_the_rule():
    """After receiving findings (Step 4), the orchestrator must echo its primary
    obligation before attempting to post. This primes the model to treat inline
    commenting as a hard requirement."""
    text = SKILL_TEXT
    assert "I must post inline comments" in text or "post inline comments" in text
    assert "specific code lines" in text


def test_skill_has_post_completion_confirmation():
    """After Step 6, the orchestrator must confirm how many inline comments
    it posted. If 0 and findings existed, it must state the review FAILED."""
    text = SKILL_TEXT
    assert "I posted" in text and "inline comments" in text
    assert "FAILED" in text or "failed" in text


def test_skill_labels_tier2_as_degraded_failure():
    """Tier 2 (body dump) must be explicitly labeled as a degraded failure mode,
    not presented as an acceptable fallback."""
    text = SKILL_TEXT
    assert "DEGRADED" in text or "degraded" in text


def test_subagent_prompt_references_lnnn_markers():
    """Subagent prompt must instruct subagents to use [LNNN] markers for line numbers."""
    text = SKILL_TEXT
    prompt_marker = "Subagent prompt template"
    prompt_start = text.find(prompt_marker)
    assert prompt_start != -1, (
        "review-pr/SKILL.md must contain a 'Subagent prompt template' section."
    )
    next_section = text.find("\n###", prompt_start + len(prompt_marker))
    prompt_section = text[prompt_start:next_section] if next_section != -1 else text[prompt_start:]
    assert "[LNNN]" in prompt_section, (
        "review-pr/SKILL.md subagent prompt must instruct subagents to use [LNNN] "
        "markers for line numbers — not compute line numbers themselves."
    )


def test_fallback_does_not_use_markdown_table():
    """Fallback body must not use a markdown table (overflows for long messages)."""
    text = SKILL_TEXT
    assert "| Line | Severity | Dimension | Message |" not in text, (
        "review-pr/SKILL.md fallback body must not use a 4-column markdown table. "
        "Long message content causes horizontal overflow. Use a bullet-list format."
    )


def test_fallback_attempts_individual_comment_posting():
    """Fallback must attempt individual per-finding comment posting before summary dump."""
    text = SKILL_TEXT
    assert any(
        kw in text
        for kw in [
            "pulls/{pr_number}/comments",
            "individual comment",
            "per-finding",
            "per finding",
        ]
    ), (
        "review-pr/SKILL.md fallback must attempt individual /pulls/{n}/comments "
        "POSTs before the summary dump. The summary dump creates a non-inline body "
        "comment that resolve-review cannot find."
    )


# --- Step 6 posting flags (regression guards for currently-correct elements) ---


def test_step6_uses_input_flag_not_field_for_comments():
    """Step 6 must prescribe --input - for the reviews payload.

    The --field approach creates one array entry per flag, not a proper JSON
    array. This was the root cause of Issue #206. Guarded here to prevent
    silent regression.

    To verify this test is effective: temporarily remove '--input -' from
    SKILL.md and confirm this test fails. Then restore it.
    """
    text = SKILL_TEXT
    assert "--input -" in text, (
        "review-pr/SKILL.md Step 6 must prescribe '--input -' for the reviews POST. "
        "Using '--field' for the comments array creates one array entry per flag "
        "instead of a proper JSON array (Issue #206 root cause)."
    )


def test_step6_does_not_prescribe_deprecated_position_field():
    """Comments payload must not include a 'position' field.

    GitHub deprecated the 'position' field in favour of 'line' + 'side'.
    The SKILL.md explicitly prohibits 'position' in the comments payload.
    Guarded here to prevent silent regression.

    To verify: add '"position":' to the Step 6 jq block in SKILL.md and
    confirm this test fails. Then restore it.
    """
    text = SKILL_TEXT
    step6_start = text.find("### Step 6")
    step7_start = text.find("### Step 7")
    assert step6_start != -1, "SKILL.md must contain a '### Step 6' heading"
    assert step7_start != -1, "SKILL.md must contain a '### Step 7' heading"
    step6_section = text[step6_start:step7_start]
    # 'position' as a JSON key in the payload (e.g. "position": or position: )
    assert not re.search(r'"position"\s*:', step6_section), (
        "review-pr/SKILL.md Step 6 comments payload must not include a 'position' "
        "field. Use 'line' + 'side: RIGHT' (the modern Reviews API)."
    )


def test_step6_payload_includes_side_right():
    """Each comment in the reviews payload must include side: 'RIGHT'.

    Omitting 'side' may cause GitHub to default to an unspecified diff side.
    Guarded here to prevent silent regression.

    To verify: remove 'side' from the jq block in SKILL.md and confirm this
    test fails. Then restore it.
    """
    text = SKILL_TEXT
    assert re.search(r"side.*RIGHT", text), (
        "review-pr/SKILL.md Step 6 comment objects must include side: 'RIGHT' "
        "to anchor comments to the new-file side of the diff."
    )


def test_step6_documents_event_mapping():
    """Step 6 must document the verdict-to-event mapping.

    approved → APPROVE, needs_human → COMMENT, changes_requested → REQUEST_CHANGES.
    Guarded here to prevent accidental mapping errors in future edits.
    """
    text = SKILL_TEXT
    assert "APPROVE" in text and "REQUEST_CHANGES" in text, (
        "review-pr/SKILL.md Step 6 must document the verdict-to-event mapping: "
        "approved → APPROVE, changes_requested → REQUEST_CHANGES."
    )
