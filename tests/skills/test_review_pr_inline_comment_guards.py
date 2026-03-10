"""Structural guards for review-pr/SKILL.md Step 6 posting mechanics.

Tests enforce the structural presence of diff-hunk validation, updated
subagent prompt guidance, and a non-table tiered fallback. Each test
makes it impossible to silently regress its guarded element.
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


def _text() -> str:
    return SKILL_PATH.read_text()


def test_skill_describes_hunk_range_parsing():
    """SKILL.md must describe parsing @@ hunk headers to extract valid line ranges."""
    text = _text()
    has_hunk_parse = "@@" in text and any(
        kw in text.lower() for kw in ["hunk", "valid_line", "line_range"]
    )
    assert has_hunk_parse, (
        "review-pr/SKILL.md must describe parsing @@ hunk headers from the diff "
        "to build valid line ranges for the GitHub Reviews API."
    )


def test_step6_describes_hunk_filtering_before_post():
    """Step 6 (or adjacent step) must describe filtering findings against hunk ranges."""
    text = _text().lower()
    assert any(kw in text for kw in ["hunk", "valid_line", "in-hunk"]), (
        "review-pr/SKILL.md must describe filtering/validating findings against diff "
        "hunk ranges before the batch review POST."
    )


def test_subagent_prompt_includes_diff_line_guidance():
    """Subagent prompt must instruct subagents to report only diff-visible line numbers."""
    text = _text().lower()
    assert any(
        kw in text
        for kw in [
            "diff hunk",
            "visible in the diff",
            "appears in the diff",
            "within the diff",
            "diff line",
            "line from the diff",
        ]
    ), (
        "review-pr/SKILL.md subagent prompt must instruct subagents to report only "
        "line numbers visible in the diff hunks — not absolute file line numbers."
    )


def test_fallback_does_not_use_markdown_table():
    """Fallback body must not use a markdown table (overflows for long messages)."""
    text = _text()
    assert "| Line | Severity | Dimension | Message |" not in text, (
        "review-pr/SKILL.md fallback body must not use a 4-column markdown table. "
        "Long message content causes horizontal overflow. Use a bullet-list format."
    )


def test_fallback_attempts_individual_comment_posting():
    """Fallback must attempt individual per-finding comment posting before summary dump."""
    text = _text()
    assert any(
        kw in text
        for kw in [
            "pulls/{pr_number}/comments",
            "pulls/{number}/comments",
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
    text = _text()
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
    text = _text()
    step6_start = text.find("### Step 6")
    step7_start = text.find("### Step 7")
    step6_section = (
        text[step6_start:step7_start] if step6_start != -1 and step7_start != -1 else text
    )
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
    text = _text()
    assert re.search(r"side.*RIGHT", text), (
        "review-pr/SKILL.md Step 6 comment objects must include side: 'RIGHT' "
        "to anchor comments to the new-file side of the diff."
    )


def test_step6_documents_event_mapping():
    """Step 6 must document the verdict-to-event mapping.

    approved → APPROVE, needs_human → COMMENT, changes_requested → REQUEST_CHANGES.
    Guarded here to prevent accidental mapping errors in future edits.
    """
    text = _text()
    assert "APPROVE" in text and "REQUEST_CHANGES" in text, (
        "review-pr/SKILL.md Step 6 must document the verdict-to-event mapping: "
        "approved → APPROVE, changes_requested → REQUEST_CHANGES."
    )
