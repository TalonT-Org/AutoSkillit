"""Contract tests for open-pr-main skill — token usage summary requirement traceability."""

from pathlib import Path

import pytest

SKILLS_DIR = Path(__file__).parents[2] / "src/autoskillit/skills_extended"
SKILL = SKILLS_DIR / "open-pr-main/SKILL.md"


@pytest.fixture()
def text():
    return SKILL.read_text()


def test_open_pr_main_skill_file_exists():
    assert SKILL.exists(), "open-pr-main/SKILL.md must exist"


def test_open_pr_main_self_retrieves_token_summary(text):
    """SKILL.md must document self-retrieval of token summary via cwd_filter."""
    assert "load_from_log_dir" in text, (
        "open-pr-main/SKILL.md must document load_from_log_dir self-retrieval"
    )
    assert "cwd_filter" in text, "open-pr-main/SKILL.md must document cwd_filter scoping key"
    assert "PIPELINE_CWD" in text, (
        "open-pr-main/SKILL.md must document PIPELINE_CWD=$(pwd) discovery"
    )


def test_open_pr_main_token_summary_in_pr_body(text):
    """Step 15 PR body template must include ## Token Usage Summary section."""
    # Section must appear after the PR body composition step heading
    body_step_idx = text.find("Step 15")
    assert body_step_idx != -1, "open-pr-main must have a Step 15"
    body_section = text[body_step_idx:]
    assert "## Token Usage Summary" in body_section, (
        "Step 15 PR body template must include '## Token Usage Summary' section"
    )


def test_open_pr_main_token_summary_conditional(text):
    """Token summary section must be conditional on non-empty TOKEN_SUMMARY_CONTENT."""
    assert "TOKEN_SUMMARY_CONTENT" in text, "open-pr-main must use TOKEN_SUMMARY_CONTENT variable"
    # The section must be gated — look for conditional language near the variable
    lower = text.lower()
    is_conditional = (
        "non-empty" in lower
        or "if token_summary_content" in lower
        or "token_summary_content is non-empty" in lower
    )
    assert is_conditional, (
        "## Token Usage Summary must be conditional on non-empty TOKEN_SUMMARY_CONTENT"
    )


def test_open_pr_main_token_summary_uses_own_temp_dir(text):
    """Token summary file must be written to temp/open-pr-main/, not temp/open-pr/."""
    assert "temp/open-pr-main/token_summary.md" in text, (
        "open-pr-main must write token summary to temp/open-pr-main/token_summary.md"
    )
    # Ensure it does NOT reference the open-pr directory
    assert "temp/open-pr/token_summary" not in text, (
        "open-pr-main must not reference temp/open-pr/token_summary.md"
    )
