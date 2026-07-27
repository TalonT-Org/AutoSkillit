"""Focused prose contract for authority-bound make-plan remediation output."""

from __future__ import annotations

from pathlib import Path

import pytest

pytestmark = [pytest.mark.layer("contracts"), pytest.mark.small]

_SKILL_MD = (
    Path(__file__).resolve().parents[2]
    / "src"
    / "autoskillit"
    / "skills_extended"
    / "make-plan"
    / "SKILL.md"
)


def _content() -> str:
    return _SKILL_MD.read_text()


def test_remediation_is_explicit_authority_bound() -> None:
    content = _content()
    assert "`audit_cycle_path`" in content
    assert "current `NO GO` head" in content
    assert "ambient `requirements_inventory.json`" in content


def test_requirements_map_uses_evaluator_vocabulary() -> None:
    content = _content()
    assert "| Requirement ID | Disposition | Implementation Step |" in content
    assert "satisfied-by-round-N" in content
    assert "carried@step" in content


def test_plan_and_report_are_immutably_associated() -> None:
    content = _content()
    assert "PlanDispositionReport" in content
    assert "associations/{verified_plan_content_digest}.json" in content
    assert "plan_disposition_path =" in content
    assert "verdict = false_positive" not in content
