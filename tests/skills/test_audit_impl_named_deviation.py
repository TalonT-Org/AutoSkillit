"""Tests for NAMED_DEVIATION classification in audit-impl slice auditor."""

from __future__ import annotations

from pathlib import Path

import pytest

pytestmark = [pytest.mark.layer("skills"), pytest.mark.small]

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SLICE_AUDITOR = _REPO_ROOT / "src/autoskillit/agents/audit-impl-slice-auditor.md"
_AUDIT_IMPL_SKILL = _REPO_ROOT / "src/autoskillit/skills_extended/audit-impl/SKILL.md"


def test_slice_auditor_defines_named_deviation():
    """Slice auditor must define NAMED_DEVIATION classification."""
    content = _SLICE_AUDITOR.read_text()
    assert "NAMED_DEVIATION" in content


def test_slice_auditor_named_deviation_describes_criteria():
    """NAMED_DEVIATION must describe when it applies (same role, different name)."""
    content = _SLICE_AUDITOR.read_text()
    idx = content.index("NAMED_DEVIATION")
    context = content[idx : idx + 500]
    assert "same" in context.lower() or "role" in context.lower()
    assert "name" in context.lower()


def test_audit_impl_has_named_deviation_postprocessing():
    """audit-impl SKILL.md must have post-processing for NAMED_DEVIATION."""
    content = _AUDIT_IMPL_SKILL.read_text()
    assert "NAMED_DEVIATION" in content


def test_audit_impl_cross_slice_guard():
    """Post-processing must check cross-slice references before downgrading."""
    content = _AUDIT_IMPL_SKILL.read_text()
    idx = content.index("NAMED_DEVIATION")
    context = content[idx : idx + 1000]
    assert "cross" in context.lower() or "other slice" in context.lower()
