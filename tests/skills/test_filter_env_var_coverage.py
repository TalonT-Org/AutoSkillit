"""Tests that retry-worktree and audit-impl skills set filter env vars for test runs."""

from pathlib import Path

SKILLS_EXTENDED = Path(__file__).resolve().parents[2] / "src" / "autoskillit" / "skills_extended"


def test_retry_worktree_step4_sets_test_filter() -> None:
    """retry-worktree Step 4 must set AUTOSKILLIT_TEST_FILTER."""
    skill_md = (SKILLS_EXTENDED / "retry-worktree" / "SKILL.md").read_text()
    # The env var must appear in the step 4 test command block
    step4_start = skill_md.index("### Step 4")
    step5_start = skill_md.index("### Step 5")
    step4_text = skill_md[step4_start:step5_start]
    assert "AUTOSKILLIT_TEST_FILTER" in step4_text


def test_retry_worktree_step4_sets_base_ref() -> None:
    """retry-worktree Step 4 must set AUTOSKILLIT_TEST_BASE_REF."""
    skill_md = (SKILLS_EXTENDED / "retry-worktree" / "SKILL.md").read_text()
    step4_start = skill_md.index("### Step 4")
    step5_start = skill_md.index("### Step 5")
    step4_text = skill_md[step4_start:step5_start]
    assert "AUTOSKILLIT_TEST_BASE_REF" in step4_text


def test_audit_impl_remediation_template_sets_test_filter() -> None:
    """audit-impl remediation Verification section must include AUTOSKILLIT_TEST_FILTER."""
    skill_md = (SKILLS_EXTENDED / "audit-impl" / "SKILL.md").read_text()
    # Locate the remediation template's Verification section
    verification_start = skill_md.index("## Verification")
    verification_end = skill_md.index("Then print:", verification_start)
    verification_text = skill_md[verification_start:verification_end]
    assert "AUTOSKILLIT_TEST_FILTER" in verification_text


def test_audit_impl_remediation_template_sets_base_ref() -> None:
    """audit-impl remediation Verification section must include AUTOSKILLIT_TEST_BASE_REF."""
    skill_md = (SKILLS_EXTENDED / "audit-impl" / "SKILL.md").read_text()
    verification_start = skill_md.index("## Verification")
    verification_end = skill_md.index("Then print:", verification_start)
    verification_text = skill_md[verification_start:verification_end]
    assert "AUTOSKILLIT_TEST_BASE_REF" in verification_text
