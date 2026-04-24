"""Tests that retry-worktree and audit-impl skills set filter env vars for test runs."""

from pathlib import Path

import pytest

SKILLS_EXTENDED = Path(__file__).resolve().parents[2] / "src" / "autoskillit" / "skills_extended"


@pytest.mark.parametrize("env_var", ["AUTOSKILLIT_TEST_FILTER", "AUTOSKILLIT_TEST_BASE_REF"])
def test_retry_worktree_step4_sets_env_var(env_var: str) -> None:
    """retry-worktree Step 4 must set AUTOSKILLIT_TEST_FILTER and AUTOSKILLIT_TEST_BASE_REF."""
    skill_md = (SKILLS_EXTENDED / "retry-worktree" / "SKILL.md").read_text()
    assert "### Step 4" in skill_md
    assert "### Step 5" in skill_md
    step4_start = skill_md.index("### Step 4")
    step5_start = skill_md.index("### Step 5")
    step4_text = skill_md[step4_start:step5_start]
    assert env_var in step4_text


@pytest.mark.parametrize("env_var", ["AUTOSKILLIT_TEST_FILTER", "AUTOSKILLIT_TEST_BASE_REF"])
def test_audit_impl_remediation_template_sets_env_var(env_var: str) -> None:
    """audit-impl Verification section must set AUTOSKILLIT_TEST_FILTER and TEST_BASE_REF."""
    skill_md = (SKILLS_EXTENDED / "audit-impl" / "SKILL.md").read_text()
    assert "## Verification" in skill_md
    verification_start = skill_md.index("## Verification")
    assert "Then print:" in skill_md[verification_start:]
    verification_end = skill_md.index("Then print:", verification_start)
    verification_text = skill_md[verification_start:verification_end]
    assert env_var in verification_text
