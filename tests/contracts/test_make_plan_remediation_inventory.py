"""Contract test: make-plan SKILL.md must contain remediation-mode inventory awareness."""

from __future__ import annotations

from pathlib import Path

import pytest

pytestmark = [pytest.mark.layer("contracts"), pytest.mark.small]

_SKILL_MD = (
    Path(__file__).resolve().parent.parent.parent
    / "src"
    / "autoskillit"
    / "skills_extended"
    / "make-plan"
    / "SKILL.md"
)


def _read_skill_md() -> str:
    return _SKILL_MD.read_text()


def _remediation_section() -> str:
    """Extract the section of make-plan SKILL.md that discusses remediation mode."""
    content = _read_skill_md()
    start = content.find("audit_remediation_mode")
    if start == -1:
        return content
    return content[start:]


def test_make_plan_references_inventory_in_remediation() -> None:
    """make-plan SKILL.md must reference requirements_inventory in remediation context."""
    section = _remediation_section()
    assert "requirements_inventory" in section, (
        "make-plan SKILL.md must reference 'requirements_inventory' in its "
        "remediation-mode section"
    )


def test_make_plan_disposition_table() -> None:
    """make-plan SKILL.md must emit a disposition table with satisfied + carried vocabulary."""
    content = _read_skill_md()
    assert "disposition" in content.lower() or "Disposition" in content, (
        "make-plan SKILL.md must emit a Disposition section/table for requirements"
    )
    assert "satisfied" in content.lower(), (
        "make-plan SKILL.md disposition vocabulary must include 'satisfied'"
    )
    assert "carried" in content.lower(), (
        "make-plan SKILL.md disposition vocabulary must include 'carried'"
    )
