"""Contract tests for generate-report SKILL.md — data provenance lifecycle."""

from pathlib import Path

SKILL_PATH = (
    Path(__file__).resolve().parent.parent.parent
    / "src"
    / "autoskillit"
    / "skills_extended"
    / "generate-report"
    / "SKILL.md"
)


def test_data_scope_statement_required() -> None:
    text = SKILL_PATH.read_text()
    assert "Data Scope Statement" in text or "data scope statement" in text.lower()


def test_data_scope_in_executive_summary() -> None:
    text = SKILL_PATH.read_text()
    assert "Executive Summary" in text


def test_metrics_provenance_check() -> None:
    text = SKILL_PATH.read_text()
    lower = text.lower()
    assert "provenance" in lower


def test_gate_enforcement_no_substitution() -> None:
    text = SKILL_PATH.read_text()
    lower = text.lower()
    assert "substitut" in lower


def test_gate_enforcement_fail_state() -> None:
    text = SKILL_PATH.read_text()
    lower = text.lower()
    assert "fail" in lower and "gate" in lower


def test_no_rust_specific_package_manager() -> None:
    """Environment section must not reference cargo tree (Rust-specific)."""
    text = SKILL_PATH.read_text()
    assert "cargo tree" not in text, (
        "generate-report/SKILL.md references 'cargo tree' (Rust-specific). "
        "Use language-agnostic package manager examples."
    )


def test_domain_adaptive_ordering_guidance() -> None:
    """SKILL.md must include guidance on domain-adaptive section ordering."""
    text = SKILL_PATH.read_text()
    lower = text.lower()
    assert "biology" in lower, (
        "generate-report/SKILL.md has no domain-adaptive ordering guidance. "
        "Add notes on biology/non-engineering section ordering conventions."
    )
    assert "domain-adaptive" in lower, (
        "generate-report/SKILL.md must mention 'domain-adaptive' section ordering "
        "to verify the guidance actually covers non-engineering conventions."
    )


def test_data_availability_section_supported() -> None:
    """SKILL.md must include an optional Data Availability section in the template."""
    text = SKILL_PATH.read_text()
    assert "Data Availability" in text, (
        "generate-report/SKILL.md template is missing an optional 'Data Availability' "
        "section. Required by biology and social science journals."
    )


def test_recommendations_or_discussion_framing() -> None:
    """SKILL.md must allow 'Discussion and Future Directions' as an alternative
    to 'Recommendations' for non-engineering domains."""
    text = SKILL_PATH.read_text()
    assert "Discussion and Future Directions" in text, (
        "generate-report/SKILL.md does not offer 'Discussion and Future Directions' "
        "as an alternative framing for the Recommendations section."
    )
