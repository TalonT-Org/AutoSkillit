"""Contract tests for the enrich-issues skill SKILL.md."""

from __future__ import annotations

from pathlib import Path

SKILL_DIR = Path(__file__).parents[2] / "src/autoskillit/skills_extended/enrich-issues"
SKILL_MD = SKILL_DIR / "SKILL.md"


def test_enrich_issues_skill_exists():
    assert SKILL_MD.exists()


def test_enrich_issues_gh_issue_edit_uses_body_file():
    """enrich-issues gh issue edit must use --body-file, not inline --body shell substitution."""
    text = SKILL_MD.read_text()
    edit_pos = text.find("gh issue edit")
    assert edit_pos != -1, "Sanity: 'gh issue edit' not found in enrich-issues"
    edit_context = text[edit_pos : edit_pos + 400]
    assert "--body-file" in edit_context, (
        "enrich-issues 'gh issue edit' must use --body-file to prevent shell-substitution "
        "truncation and LLM body recomposition"
    )


def test_enrich_issues_body_file_uses_autoskillit_temp():
    """enrich-issues must write edit body to AUTOSKILLIT_TEMP/enrich-issues/."""
    text = SKILL_MD.read_text()
    assert "AUTOSKILLIT_TEMP" in text and "enrich-issues" in text, (
        "enrich-issues must write issue body to {{AUTOSKILLIT_TEMP}}/enrich-issues/ "
        "before calling gh issue edit --body-file"
    )
