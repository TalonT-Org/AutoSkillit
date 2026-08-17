"""Contract tests for triage-issues Step 3 recipe classification behavior."""

from pathlib import Path

import pytest

pytestmark = [pytest.mark.layer("contracts"), pytest.mark.medium]

SKILLS_DIR = Path(__file__).parents[2] / "src/autoskillit/skills_extended"
TRIAGE_SKILL = SKILLS_DIR / "triage-issues/SKILL.md"


# ---------------------------------------------------------------------------
# TRIAGE MISCLASSIFICATION: Gap 1b — validated audit report signal in Step 3
# ---------------------------------------------------------------------------


def test_triage_step3_validated_audit_report_signal():
    """Step 3 must have an explicit signal for Validated Audit Reports → implementation."""
    text = TRIAGE_SKILL.read_text()
    step3_pos = text.find("### Step 3:")
    assert step3_pos != -1, "Step 3 Recipe Classification not found"
    step3b_pos = text.find("### Step 3b:", step3_pos)
    step3_section = (
        text[step3_pos:step3b_pos] if step3b_pos != -1 else text[step3_pos : step3_pos + 3000]
    )
    has_audit_signal = (
        "Validated Audit Report" in step3_section or "validated audit" in step3_section.lower()
    )
    assert has_audit_signal, (
        "triage-issues Step 3 must have an explicit signal for issues with 'Validated Audit "
        "Report' in the title routing to implementation — not fall through to ambiguous-scope "
        "fallback"
    )


def test_triage_step3_scope_alone_not_remediation():
    """Step 3 must document that scope alone must not override behavioral signals."""
    text = TRIAGE_SKILL.read_text()
    step3_pos = text.find("### Step 3:")
    assert step3_pos != -1
    step3b_pos = text.find("### Step 3b:", step3_pos)
    step3_section = (
        text[step3_pos:step3b_pos] if step3b_pos != -1 else text[step3_pos : step3_pos + 3000]
    )
    # Must explicitly call out that scope/volume alone does not trigger remediation
    scope_guarded = (
        "scope alone" in step3_section.lower()
        or "number of findings" in step3_section.lower()
        or "large scope" in step3_section.lower()
    )
    assert scope_guarded, (
        "Step 3 must clarify that issue scope (high finding count) alone must not route "
        "a validated audit report to remediation"
    )
