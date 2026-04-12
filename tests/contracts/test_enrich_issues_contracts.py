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
    # Find the Apply or Preview step (Step 5e) where the mutation happens
    apply_pos = text.find("5e. Apply or Preview")
    assert apply_pos != -1, "Sanity: 'Apply or Preview' section not found in enrich-issues"
    apply_section = text[apply_pos : apply_pos + 800]
    assert "gh issue edit" in apply_section, "Sanity: 'gh issue edit' not found in Step 5e"
    assert "--body-file" in apply_section, (
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
