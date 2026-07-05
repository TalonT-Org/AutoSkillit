"""Contract test: dry-walkthrough SKILL.md plan-vs-inventory gate (Step 4.7)."""

from __future__ import annotations

import re
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


def _step47_section() -> str:
    content = _read_skill_md()
    return content[content.find("### Step 4.7") : content.find("### Step 5:")]


def test_dry_walkthrough_has_inventory_coverage_step() -> None:
    """dry-walkthrough SKILL.md must contain Step 4.7 titled Plan-vs-Inventory Coverage Check."""
    content = _read_skill_md()
    assert re.search(
        r"###\s+Step\s+4\.7[\s:].*Plan-vs-Inventory\s+Coverage\s+Check",
        content,
        re.DOTALL,
    ), (
        "dry-walkthrough/SKILL.md must contain a '### Step 4.7' section titled "
        "'Plan-vs-Inventory Coverage Check'"
    )


def test_dry_walkthrough_inventory_coverage_positioned() -> None:
    """Step 4.7 must appear between Step 4.6 and Step 5."""
    content = _read_skill_md()
    step46_pos = content.find("### Step 4.6")
    step47_pos = content.find("### Step 4.7")
    step5_pos = content.find("### Step 5:")
    assert step46_pos != -1, "Step 4.6 section must exist"
    assert step47_pos != -1, "Step 4.7 section must exist"
    assert step5_pos != -1, "Step 5 section must exist"
    assert step46_pos < step47_pos < step5_pos, (
        "Step 4.7 must be positioned between Step 4.6 and Step 5 in dry-walkthrough/SKILL.md"
    )


def test_dry_walkthrough_inventory_coverage_references_file() -> None:
    """Step 4.7 must reference requirements_inventory.json in AUTOSKILLIT_TEMP."""
    section = _step47_section()
    assert "requirements_inventory.json" in section, (
        "dry-walkthrough SKILL.md Step 4.7 must reference 'requirements_inventory.json'"
    )
    assert "AUTOSKILLIT_TEMP" in section, (
        "dry-walkthrough SKILL.md Step 4.7 must reference AUTOSKILLIT_TEMP for inventory path"
    )


def test_dry_walkthrough_inventory_coverage_guard_skip() -> None:
    """Step 4.7 must specify graceful skip when inventory file does not exist."""
    section = _step47_section()
    assert re.search(
        r"omit|not present|does not exist",
        section,
        re.IGNORECASE,
    ), (
        "dry-walkthrough SKILL.md Step 4.7 must specify graceful skip when "
        "requirements_inventory.json does not exist"
    )


def test_dry_walkthrough_inventory_coverage_two_dispositions() -> None:
    """Step 4.7 must define two dispositions: satisfied and carried."""
    section = _step47_section()
    assert re.search(r"satisfied", section, re.IGNORECASE), (
        "dry-walkthrough SKILL.md Step 4.7 must define a 'satisfied' disposition "
        "for requirements verified complete by prior audit round"
    )
    assert re.search(r"carried", section, re.IGNORECASE), (
        "dry-walkthrough SKILL.md Step 4.7 must define a 'carried' disposition "
        "for requirements addressed by new Implementation Steps"
    )


def test_dry_walkthrough_inventory_coverage_blocks_on_unmapped() -> None:
    """Step 4.7 must specify blocking/failing when requirements are UNMAPPED."""
    section = _step47_section()
    assert re.search(
        r"UNMAPPED|FAIL|block|do not proceed|Stop execution",
        section,
        re.IGNORECASE,
    ), (
        "dry-walkthrough SKILL.md Step 4.7 must specify blocking behavior when "
        "requirements are not mapped to plan steps"
    )


def test_dry_walkthrough_inventory_coverage_deterministic_stop() -> None:
    """Step 4.7 must contain an explicit deterministic stop directive."""
    section = _step47_section()
    assert "Stop execution" in section or "do not proceed to Step 5" in section, (
        "dry-walkthrough SKILL.md Step 4.7 must contain an explicit deterministic "
        "stop directive on UNMAPPED failure"
    )


def test_dry_walkthrough_inventory_coverage_composes_with_46() -> None:
    """Step 4.7 must reference composition with Step 4.6."""
    section = _step47_section()
    assert "4.6" in section or "compose" in section.lower(), (
        "dry-walkthrough SKILL.md Step 4.7 must reference composition with Step 4.6"
    )
