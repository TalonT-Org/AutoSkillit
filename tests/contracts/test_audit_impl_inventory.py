"""Contract test: audit-impl SKILL.md must reference pinned requirement inventory."""

from __future__ import annotations

from pathlib import Path

import pytest

pytestmark = [pytest.mark.layer("contracts"), pytest.mark.small]

_SKILL_MD = (
    Path(__file__).resolve().parent.parent.parent
    / "src"
    / "autoskillit"
    / "skills_extended"
    / "audit-impl"
    / "SKILL.md"
)


def _read_skill_md() -> str:
    return _SKILL_MD.read_text()


def test_audit_impl_references_inventory_persistence() -> None:
    """audit-impl SKILL.md must reference writing a requirements
    inventory to AUTOSKILLIT_TEMP on round 1 and reading it on round ≥2."""
    content = _read_skill_md()
    assert "requirements_inventory" in content, (
        "audit-impl/SKILL.md must reference 'requirements_inventory'"
    )
    assert "AUTOSKILLIT_TEMP" in content or "{{AUTOSKILLIT_TEMP}}" in content, (
        "audit-impl/SKILL.md must reference AUTOSKILLIT_TEMP for inventory file location"
    )


def test_audit_impl_references_round_detection() -> None:
    """audit-impl must detect round ≥2 by inventory file existence."""
    content = _read_skill_md()
    assert "round" in content.lower(), "audit-impl/SKILL.md must reference round detection"
    assert "exist" in content.lower() or "presence" in content.lower(), (
        "audit-impl/SKILL.md must reference checking for existing inventory file"
    )


def test_audit_impl_references_union_extraction() -> None:
    """Round-1 extraction must use ≥2 independent extractors with union merge."""
    content = _read_skill_md()
    assert "union" in content.lower() or "independent" in content.lower(), (
        "audit-impl/SKILL.md must reference union extraction or independent extractors"
    )
