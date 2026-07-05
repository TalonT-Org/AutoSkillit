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


def _step1_section() -> str:
    """Extract Step 1 section of audit-impl SKILL.md."""
    content = _read_skill_md()
    return content[content.find("### Step 1") : content.find("### Step 2")]


def test_audit_impl_references_inventory_persistence() -> None:
    """Step 1 must reference writing requirements_inventory.json to AUTOSKILLIT_TEMP."""
    section = _step1_section()
    assert "requirements_inventory.json" in section, (
        "audit-impl SKILL.md Step 1 must reference 'requirements_inventory.json'"
    )
    assert "write" in section.lower() or "persist" in section.lower(), (
        "audit-impl SKILL.md Step 1 must reference writing or persisting the inventory"
    )
    assert "AUTOSKILLIT_TEMP" in section or "{{AUTOSKILLIT_TEMP}}" in section, (
        "audit-impl SKILL.md Step 1 must reference AUTOSKILLIT_TEMP for inventory path"
    )


def test_audit_impl_references_round_detection() -> None:
    """Step 1 must detect round >=2 by inventory file existence."""
    section = _step1_section()
    assert "round" in section.lower(), "audit-impl SKILL.md Step 1 must reference round detection"
    assert "exist" in section.lower() or "presence" in section.lower(), (
        "audit-impl SKILL.md Step 1 must reference checking for existing inventory file"
    )


def test_audit_impl_references_union_extraction() -> None:
    """Round-1 extraction must use >=2 independent extractors with union merge."""
    content = _read_skill_md()
    assert "union" in content.lower() or "independent" in content.lower(), (
        "audit-impl SKILL.md must reference union extraction or independent extractors"
    )


def test_audit_impl_inventory_schema_fields() -> None:
    """Step 1 inventory schema must include all required fields."""
    section = _step1_section()
    for field in ("id", "text", "source_file", "source_line", "source_section"):
        assert field in section, (
            f"audit-impl SKILL.md Step 1 inventory schema must include '{field}' field"
        )
