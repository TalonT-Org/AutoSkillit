"""Aggregate coverage does not inspect sibling plan text."""

from __future__ import annotations

from pathlib import Path

import pytest

from autoskillit.core import (
    CoverageStatus,
    InventoryMode,
    PlanSetBindResult,
    PlanSetRejectReason,
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


@pytest.mark.parametrize("coverage_gaps", ("", " \n\t"))
def test_coverage_failure_requires_nonblank_feedback(coverage_gaps: str) -> None:
    with pytest.raises(ValueError):
        PlanSetBindResult(
            False,
            PlanSetRejectReason.COVERAGE_FAILED,
            "plan-set coverage has gaps",
            coverage_gaps=coverage_gaps,
        )


@pytest.mark.parametrize(
    ("success", "reason", "coverage_gaps"),
    [
        (True, PlanSetRejectReason.NO_PARTS, ""),
        (False, None, ""),
        (True, None, "R2 [unassigned]"),
        (False, PlanSetRejectReason.NO_PARTS, "R2 [unassigned]"),
    ],
)
def test_bind_result_rejects_inconsistent_success_fields(
    success: bool, reason: PlanSetRejectReason | None, coverage_gaps: str
) -> None:
    with pytest.raises(ValueError):
        PlanSetBindResult(success, reason, "", coverage_gaps=coverage_gaps)


def test_coverage_feedback_invariant_applies_only_to_coverage_failures() -> None:
    failed = PlanSetBindResult(
        False,
        PlanSetRejectReason.COVERAGE_FAILED,
        "plan-set coverage has gaps",
        coverage_gaps="R2 [unassigned]",
    )
    other_failure = PlanSetBindResult(False, PlanSetRejectReason.NO_PARTS, "no plan parts")
    success = PlanSetBindResult(True, None, "")

    assert failed.coverage_gaps == "R2 [unassigned]"
    assert other_failure.coverage_gaps == ""
    assert success.coverage_gaps == ""
