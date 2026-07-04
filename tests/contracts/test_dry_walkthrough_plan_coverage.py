"""Contract test: dry-walkthrough SKILL.md must contain plan-vs-inventory coverage gate."""

from __future__ import annotations

from pathlib import Path

import pytest

pytestmark = [pytest.mark.layer("contracts"), pytest.mark.small]

_SKILL_MD = (
    Path(__file__).resolve().parent.parent.parent
    / "src"
    / "autoskillit"
    / "skills_extended"
    / "dry-walkthrough"
    / "SKILL.md"
)


def _read_skill_md() -> str:
    return _SKILL_MD.read_text()


def test_dry_walkthrough_references_plan_vs_inventory() -> None:
    """dry-walkthrough must have a Step 4.7 for plan-vs-inventory coverage mode
    in remediation context."""
    content = _read_skill_md()
    assert "requirements_inventory" in content, (
        "dry-walkthrough/SKILL.md must reference 'requirements_inventory'"
    )
    assert "Step 4.7" in content or "4.7" in content, (
        "dry-walkthrough/SKILL.md must reference Step 4.7 for plan-vs-inventory coverage"
    )


def test_dry_walkthrough_coverage_handles_plan_vs_inventory() -> None:
    """Step 4.7 must read requirements_inventory.json and check
    every original requirement is either satisfied or carried."""
    content = _read_skill_md()
    assert "requirements_inventory.json" in content, (
        "dry-walkthrough/SKILL.md must reference 'requirements_inventory.json' filename"
    )
    assert "plan-vs-inventory" in content.lower() or "plan-vs-plan" in content.lower(), (
        "dry-walkthrough/SKILL.md must reference 'plan-vs-inventory' or 'plan-vs-plan' mode"
    )
