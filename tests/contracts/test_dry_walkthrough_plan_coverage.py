"""Focused prose contract for dry-walkthrough Step 4.7."""

from __future__ import annotations

from pathlib import Path

import pytest

pytestmark = [pytest.mark.layer("contracts"), pytest.mark.small]

_SKILL_MD = (
    Path(__file__).resolve().parents[2]
    / "src"
    / "autoskillit"
    / "skills_extended"
    / "dry-walkthrough"
    / "SKILL.md"
)


def _step47() -> str:
    content = _SKILL_MD.read_text()
    start = content.index("### Step 4.7")
    end = content.index("### Step 5:")
    return content[start:end]


def test_step47_consumes_preflight_and_preserves_rows() -> None:
    section = _step47()
    assert "`audit_cycle_inventory` preflight evidence" in section
    assert "`satisfied-by-round-N`" in section
    assert "`carried@step`" in section
    assert "requirements_inventory.json" not in section


def test_step47_distinguishes_absence_and_stops_on_reject() -> None:
    section = _step47()
    assert "Absence is distinct from an authoritative\n   empty inventory" in section
    assert "Stop execution — do not proceed to Step 5" in section


def test_step5_cannot_self_heal_evaluator_results() -> None:
    content = _SKILL_MD.read_text()
    step5 = content[content.index("### Step 5:") : content.index("### Step 6:")]
    assert "Never add carry-forward padding" in step5
    assert "Step 5 cannot\n   override the evaluator" in step5
