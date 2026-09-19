"""Aggregate coverage does not inspect sibling plan text."""

from __future__ import annotations

from pathlib import Path

import pytest

from autoskillit.core import (
    CoverageStatus,
    InventoryMode,
    evaluate_coverage,
    extract_requirement_inventory,
    parse_part_allocation,
)

pytestmark = [pytest.mark.layer("core"), pytest.mark.small]

_FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "plan_set"


def test_split_and_missing_coverage() -> None:
    split = _FIXTURES / "split_r1_r2"
    inventory = extract_requirement_inventory((split / "issue.md").read_text(), issue_number=1)
    rows = {
        "P1": parse_part_allocation((split / "part_a.md").read_text(), part_key="P1"),
        "P2": parse_part_allocation((split / "part_b.md").read_text(), part_key="P2"),
    }
    assert (
        evaluate_coverage(inventory.mode, inventory.requirements, rows).status
        is CoverageStatus.PASS
    )

    missing = _FIXTURES / "missing_r2"
    missing_rows = {
        "P1": parse_part_allocation((missing / "part_a.md").read_text(), part_key="P1"),
        "P2": parse_part_allocation((missing / "part_b.md").read_text(), part_key="P2"),
    }
    result = evaluate_coverage(inventory.mode, inventory.requirements, missing_rows)
    assert result.status is CoverageStatus.FAIL
    assert result.unassigned == ("R2",)


def test_unenumerated_parts_require_planner_obligations() -> None:
    assert evaluate_coverage(
        InventoryMode.UNENUMERATED, (), {"P1": (), "P2": ()}
    ).parts_without_obligations == ("P1", "P2")
