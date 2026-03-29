from pathlib import Path

SKILL_MD = Path(__file__).parents[2] / "src/autoskillit/skills_extended/audit-arch/SKILL.md"


def test_selfvalidation_pass_section_exists():
    """T-AA-011: Self-Validation Pass step exists in SKILL.md."""
    text = SKILL_MD.read_text()
    assert "Self-Validation Pass" in text, (
        "audit-arch SKILL.md must contain a 'Self-Validation Pass' step "
        "in the Audit Workflow (IMP-006 requirement)"
    )


def test_selfvalidation_precedes_write_report():
    """T-AA-012: Self-Validation Pass appears before 'Write report'."""
    text = SKILL_MD.read_text()
    selfval_idx = text.index("Self-Validation Pass")
    report_idx = text.index("**Write report**")
    assert selfval_idx < report_idx, (
        "Self-Validation Pass must appear BEFORE 'Write report' in the Audit Workflow"
    )


def test_selfvalidation_follows_consolidate_findings():
    """T-AA-013: Self-Validation Pass appears after 'Consolidate findings'."""
    text = SKILL_MD.read_text()
    consolidate_idx = text.index("Consolidate findings")
    selfval_idx = text.index("Self-Validation Pass")
    assert consolidate_idx < selfval_idx, (
        "Self-Validation Pass must appear AFTER 'Consolidate findings' in the Audit Workflow"
    )


def test_selfvalidation_requires_high_critical_reread():
    """T-AA-014: Spot-check must require re-reading HIGH and CRITICAL findings."""
    text = SKILL_MD.read_text()
    selfval_idx = text.index("Self-Validation Pass")
    after_selfval = text[selfval_idx : selfval_idx + 1500]
    assert "HIGH" in after_selfval or "CRITICAL" in after_selfval, (
        "Self-Validation Pass must mention HIGH or CRITICAL findings in its "
        "spot-check instructions (IMP-006a)"
    )


def test_selfvalidation_requires_concrete_class_check():
    """T-AA-015: Spot-check must require reading the concrete class for resource-leak/data-loss."""
    text = SKILL_MD.read_text()
    selfval_idx = text.index("Self-Validation Pass")
    after_selfval = text[selfval_idx : selfval_idx + 1500].lower()
    assert "concrete" in after_selfval, (
        "Self-Validation Pass must require reading the concrete class implementation "
        "(not just the Protocol) for resource-leak and data-loss findings (IMP-006b)"
    )


def test_selfvalidation_requires_internal_note():
    """T-AA-016: Validation pass must require producing a CONFIRMED or REVISED note."""
    text = SKILL_MD.read_text()
    selfval_idx = text.index("Self-Validation Pass")
    after_selfval = text[selfval_idx : selfval_idx + 1500]
    assert "CONFIRMED" in after_selfval or "REVISED" in after_selfval, (
        "Self-Validation Pass must require producing an internal CONFIRMED or REVISED "
        "note for each reviewed finding before the report is written (IMP-006d)"
    )
