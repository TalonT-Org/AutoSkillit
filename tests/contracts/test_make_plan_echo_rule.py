"""Contract test: make-plan SKILL.md must contain requirement echo validation rule."""

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


def test_make_plan_skill_contains_echo_rule() -> None:
    """make-plan SKILL.md must mandate that every behavioral requirement
    in Design Decisions / Summary / Proposed Architecture is echoed as
    an explicit Implementation Steps directive."""
    content = _read_skill_md()
    assert "echo" in content.lower() or "traced" in content.lower(), (
        "make-plan/SKILL.md must contain an echo validation rule requiring behavioral "
        "requirements to be traced to Implementation Steps"
    )
    assert "Implementation Steps" in content
    assert "Design Decisions" in content or "Summary" in content


def test_make_plan_template_has_traceability() -> None:
    """Plan template must include a requirement traceability section
    that maps each prose constraint to an Implementation Steps item."""
    content = _read_skill_md()
    assert "Requirements Map" in content or "Requirements Traceability" in content, (
        "make-plan/SKILL.md must include a Requirements Map or Requirements Traceability section"
    )
