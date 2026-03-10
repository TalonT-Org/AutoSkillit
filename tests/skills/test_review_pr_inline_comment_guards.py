"""Structural guards for review-pr/SKILL.md Step 6 posting mechanics.

Tests enforce the structural presence of diff-hunk validation, updated
subagent prompt guidance, and a non-table tiered fallback. Each test
makes it impossible to silently regress its guarded element.
"""

from pathlib import Path

SKILL_PATH = (
    Path(__file__).parent.parent.parent
    / "src" / "autoskillit" / "skills" / "review-pr" / "SKILL.md"
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
