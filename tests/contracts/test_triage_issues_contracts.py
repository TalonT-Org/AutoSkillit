"""Contract tests for triage-issues body-file safety (gh issue edit --body-file)."""

from __future__ import annotations

from pathlib import Path

SKILL_DIR = Path(__file__).parents[2] / "src/autoskillit/skills_extended/triage-issues"
SKILL_MD = SKILL_DIR / "SKILL.md"


def test_triage_issues_skill_exists():
    assert SKILL_MD.exists(), f"SKILL.md not found at {SKILL_MD}"


def test_triage_issues_gh_issue_edit_uses_body_file():
    """triage-issues gh issue edit must use --body-file, not inline --body shell substitution."""
    text = SKILL_MD.read_text()
    edit_pos = text.find("gh issue edit")
    assert edit_pos != -1, "Sanity: 'gh issue edit' not found in triage-issues"
    edit_context = text[edit_pos : edit_pos + 400]
    assert "--body-file" in edit_context, (
        "triage-issues 'gh issue edit' must use --body-file to prevent shell-substitution "
        "truncation and LLM body recomposition"
    )


def test_triage_issues_body_file_uses_autoskillit_temp():
    """triage-issues must write edit body to AUTOSKILLIT_TEMP/triage-issues/."""
    text = SKILL_MD.read_text()
    assert "AUTOSKILLIT_TEMP" in text, (
        "triage-issues must write issue body to {{AUTOSKILLIT_TEMP}}/triage-issues/ "
        "before calling gh issue edit --body-file"
    )
