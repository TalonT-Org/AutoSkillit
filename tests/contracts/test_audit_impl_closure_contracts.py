"""Contract test: audit-impl SKILL.md must document closure mode behavior."""

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


def _closure_section() -> str:
    """Extract Closure Mode section of audit-impl SKILL.md."""
    content = _read_skill_md()
    start = content.find("## Closure Mode")
    assert start != -1, "Closure Mode section must exist in audit-impl SKILL.md"
    next_section = content.find("## ", start + 1)
    if next_section == -1:
        return content[start:]
    return content[start:next_section]


def test_skill_md_documents_closure_mode_arguments() -> None:
    """SKILL.md must reference closure_authority_path and closure_authority_hash."""
    content = _read_skill_md()
    assert "closure_authority_path" in content, (
        "audit-impl SKILL.md must reference 'closure_authority_path'"
    )
    assert "closure_authority_hash" in content, (
        "audit-impl SKILL.md must reference 'closure_authority_hash'"
    )


def test_skill_md_documents_xor_fail_closed() -> None:
    """Closure mode section must mention XOR/fail-closed behavior."""
    section = _closure_section()
    assert "XOR" in section or "xor" in section or "exactly one" in section.lower(), (
        "audit-impl SKILL.md Closure Mode section must mention XOR/exactly-one validation"
    )
    assert "fail closed" in section.lower() or "emit error" in section.lower(), (
        "audit-impl SKILL.md Closure Mode section must mention fail-closed behavior"
    )


def test_skill_md_documents_inventory_isolation() -> None:
    """Closure mode section must state never touch requirements_inventory.json."""
    section = _closure_section()
    assert "requirements_inventory.json" in section, (
        "audit-impl SKILL.md Closure Mode section must reference requirements_inventory.json"
    )
    assert "NEVER" in section or "never" in section.lower(), (
        "audit-impl SKILL.md Closure Mode section must contain NEVER/never directive"
    )


def test_skill_md_documents_canonical_report() -> None:
    """Closure mode section must describe canonical JSON report production."""
    section = _closure_section()
    assert "closure_report.json" in section or "canonical" in section.lower(), (
        "audit-impl SKILL.md Closure Mode section must reference closure_report.json"
    )
    assert "schema" in section.lower() or "ClosureReport" in section, (
        "audit-impl SKILL.md Closure Mode section must reference schema"
    )


def test_skill_md_documents_secure_file_handling() -> None:
    """SKILL.md closure mode section must reference containment checks."""
    section = _closure_section()
    assert (
        "containment" in section.lower()
        or "symlink" in section.lower()
        or "secure" in section.lower()
    ), "audit-impl SKILL.md Closure Mode section must reference containment or symlink checks"
