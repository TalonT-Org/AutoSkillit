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
    / "skills_extended"
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
    step6_start = text.find("### Step 6")
    step65_start = text.find("### Step 6.5")
    assert step6_start != -1
    assert step65_start != -1
    step6_section = text[step6_start:step65_start]
    assert "--input -" in step6_section, (
        "Step 6 must use '--input -' for the reviews POST payload. "
        "This flag must appear within Step 6 specifically (not elsewhere in SKILL.md)."
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


# --- Section-scoped regression guards ---


def test_step6_builds_comments_from_jq_argjson():
    """Step 6 must contain the jq -n --argjson pattern for building COMMENTS_JSON."""
    text = SKILL_TEXT
    step6_start = text.find("### Step 6")
    step65_start = text.find("### Step 6.5")
    assert step6_start != -1
    assert step65_start != -1
    step6_section = text[step6_start:step65_start]
    assert "jq -n --argjson findings" in step6_section, (
        "Step 6 must contain 'jq -n --argjson findings' for building COMMENTS_JSON "
        "from FILTERED_FINDINGS. Without this, inline comment construction is unguided."
    )


def test_step6_uses_filtered_findings_as_comment_source():
    """Step 6 must build COMMENTS_JSON from FILTERED_FINDINGS, not all findings."""
    text = SKILL_TEXT
    step6_start = text.find("### Step 6")
    step65_start = text.find("### Step 6.5")
    assert step6_start != -1
    assert step65_start != -1
    step6_section = text[step6_start:step65_start]
    assert "FILTERED_FINDINGS" in step6_section, (
        "Step 6 must reference FILTERED_FINDINGS as the source for inline comments. "
        "Using all findings bypasses hunk-range validation from Step 4."
    )


def test_step65_positioned_between_step6_and_step7():
    """Step 6.5 (Post-Completion Confirmation) must exist between Step 6 and Step 7."""
    text = SKILL_TEXT
    step6_idx = text.find("### Step 6:")
    if step6_idx == -1:
        step6_idx = text.find("### Step 6")
    step65_idx = text.find("### Step 6.5")
    step7_idx = text.find("### Step 7")
    assert step6_idx != -1, "SKILL.md must contain Step 6"
    assert step65_idx != -1, "SKILL.md must contain Step 6.5"
    assert step7_idx != -1, "SKILL.md must contain Step 7"
    assert step6_idx < step65_idx < step7_idx, (
        f"Step 6.5 must be positioned between Step 6 and Step 7. "
        f"Found: Step 6 at {step6_idx}, Step 6.5 at {step65_idx}, Step 7 at {step7_idx}"
    )


def test_step65_contains_do_not_proceed_gate():
    """Step 6.5 must contain 'Do not proceed to Step 7' to prevent bypassing confirmation."""
    text = SKILL_TEXT
    step65_start = text.find("### Step 6.5")
    step7_start = text.find("### Step 7")
    assert step65_start != -1
    assert step7_start != -1
    step65_section = text[step65_start:step7_start]
    assert "do not proceed to step 7" in step65_section.lower(), (
        "Step 6.5 must contain 'Do not proceed to Step 7' as a hard gate. "
        "Without this, a model that posted 0 inline comments can skip to verdict."
    )


def test_skill_prohibits_local_file_paths_in_review_body():
    """SKILL.md must explicitly prohibit referencing local file paths in review body."""
    text = SKILL_TEXT.lower()
    assert any(
        phrase in text
        for phrase in [
            "never reference local file path",
            "do not reference local file path",
            "do not include local file path",
            "never include local file path",
            "must not reference local",
            "must not include local",
        ]
    ), (
        "SKILL.md must explicitly prohibit referencing local file paths "
        "(e.g., .autoskillit/temp/...) in the review body or inline comments. "
        "GitHub readers cannot access local filesystem paths."
    )


def test_step8_ordering_enforcement():
    """Step 8 must contain ordering enforcement — it must execute after Steps 6 and 7."""
    text = SKILL_TEXT
    step8_start = text.find("### Step 8")
    assert step8_start != -1
    step8_section = text[step8_start:]
    step8_lower = step8_section.lower()
    assert any(
        phrase in step8_lower
        for phrase in [
            "after step",
            "after steps 6 and 7",
            "after posting",
            "must execute after",
        ]
    ), (
        "Step 8 must contain explicit ordering enforcement stating it runs "
        "after Steps 6 and 7. Writing the summary file before posting inline "
        "comments anchors the model to treating the file as primary output."
    )


def test_mandatory_echo_positioned_between_step4_and_step5():
    """The mandatory 'I have N findings' echo must appear between Step 4 and Step 5."""
    text = SKILL_TEXT
    step4_idx = text.find("### Step 4")
    step5_idx = text.find("### Step 5")
    assert step4_idx != -1
    assert step5_idx != -1
    between = text[step4_idx:step5_idx]
    assert "my primary job is to post inline comments" in between.lower(), (
        "The mandatory echo ('My primary job is to post inline comments') must "
        "appear between Step 4 and Step 5. This forces the model to acknowledge "
        "its inline comment obligation before proceeding to verdict determination."
    )
    assert "do not proceed to step 5" in between.lower(), (
        "The 'Do not proceed to Step 5' gate must appear between Step 4 and Step 5."
    )


def test_review_pr_http200_success_signal():
    """HTTP 200 must be treated as review-post success; response body must not be inspected."""
    skill_md = SKILL_TEXT
    assert "HTTP 200" in skill_md or "http 200" in skill_md.lower()
    lower = skill_md.lower()
    assert (
        "do not inspect" in lower
        or "do not check" in lower
        or "regardless of response body" in lower
    )


def test_review_pr_tier1_fallback_has_delay():
    """Tier 1 fallback loop must include sleep 1 between individual POSTs."""
    skill_md = SKILL_TEXT
    tier1_idx = skill_md.find("Fallback Tier 1")
    assert tier1_idx >= 0, "Tier 1 fallback section not found in skill"
    tier2_idx = skill_md.find("Tier 2", tier1_idx)
    tier1_section = skill_md[tier1_idx:tier2_idx] if tier2_idx >= 0 else skill_md[tier1_idx:]
    assert "sleep 1" in tier1_section or "sleep(1)" in tier1_section, (
        "Tier 1 fallback loop must include sleep 1 between individual POST calls"
    )
