"""Tests for false_positive verdict support in make-plan SKILL.md."""

from __future__ import annotations

from pathlib import Path

import pytest

pytestmark = [pytest.mark.layer("skills"), pytest.mark.small]

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SKILL_MD = _REPO_ROOT / "src/autoskillit/skills_extended/make-plan/SKILL.md"


def test_make_plan_skill_mentions_false_positive():
    """SKILL.md must mention false_positive verdict."""
    content = _SKILL_MD.read_text()
    assert "false_positive" in content


def test_make_plan_skill_mentions_audit_remediation_mode():
    """SKILL.md must describe audit_remediation_mode guard."""
    content = _SKILL_MD.read_text()
    assert "audit_remediation_mode" in content


def test_make_plan_skill_restricts_false_positive_to_remediation():
    """false_positive must only be emittable in remediation context."""
    content = _SKILL_MD.read_text()
    fp_idx = content.index("false_positive")
    surrounding = content[max(0, fp_idx - 500) : fp_idx + 1000]
    assert "audit_remediation_mode" in surrounding or "remediation" in surrounding.lower()
