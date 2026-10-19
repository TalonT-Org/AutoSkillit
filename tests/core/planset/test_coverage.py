"""Aggregate coverage does not inspect sibling plan text."""

from __future__ import annotations

from pathlib import Path

import pytest

from autoskillit.core import (
    AllocationKind,
    AllocationRowDef,
    CoverageResultDef,
    CoverageStatus,
    InventoryMode,
    IssueSnapshotRef,
    PlanPartRef,
    PlanSetAuthority,
    PlanSetBindingMode,
    PlanSetBindResult,
    PlanSetRejectReason,
    PlanSetState,
    RequirementDef,
    assigned_requirements,
    compute_bytes_hash,
    evaluate_coverage,
    extract_requirement_inventory,
    parse_part_allocation,
)

pytestmark = [pytest.mark.layer("core"), pytest.mark.small]

_FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "plan_set"


def _stage_row(
    requirement_id: str, part_key: str, obligation: str = "pr_opened"
) -> AllocationRowDef:
    from autoskillit.core import StageObligation

    return AllocationRowDef(
        requirement_id,
        part_key,
        AllocationKind.STAGE,
        None,
        stage_obligation=StageObligation(obligation),
    )


def _enumerated_authority(
    tmp_path: Path,
    requirements: tuple[RequirementDef, ...],
    allocations: tuple[AllocationRowDef, ...],
) -> PlanSetAuthority:
    data = b"# Plan\n"
    part = tmp_path / "part.md"
    part.write_bytes(data)
    issue_bytes = b"# Issue\n"
    return PlanSetAuthority.create(
        binding_mode=PlanSetBindingMode.RECIPE,
        execution_generation="execution",
        kitchen_id="kitchen",
        dispatch_id="",
        plan_set_authority_id="planset-stage-tests",
        revision=1,
        parent_authority_digest=None,
        state=PlanSetState.SEALED,
        inventory_mode=InventoryMode.ENUMERATED,
        issue=IssueSnapshotRef(
            "https://github.com/example/repo/issues/1",
            1,
            str(tmp_path / "issue.md"),
            len(issue_bytes),
            compute_bytes_hash(issue_bytes),
            "2026-09-18T00:00:00Z",
        ),
        requirements=requirements,
        parts=(
            PlanPartRef(
                1,
                "P1",
                "A",
                str(part),
                part.stat().st_size,
                compute_bytes_hash(part.read_bytes()),
            ),
        ),
        allocations=allocations,
        coverage=CoverageResultDef(CoverageStatus.PASS),
        unparsed_marker_lines=(),
        generated_at="2026-09-18T00:00:00Z",
    )


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


def test_stage_row_assigns_enumerated_requirement() -> None:
    split = _FIXTURES / "split_r1_r2"
    inventory = extract_requirement_inventory((split / "issue.md").read_text(), issue_number=1)
    rows = {
        "P1": (_stage_row("R1", "P1"),),
        "P2": (AllocationRowDef("R2", "P2", AllocationKind.OWNED, "Step 2"),),
    }

    result = evaluate_coverage(inventory.mode, inventory.requirements, rows)

    assert result.status is CoverageStatus.PASS


def test_split_code_and_stage_ownership_passes_coverage() -> None:
    split = _FIXTURES / "split_r1_r2"
    inventory = extract_requirement_inventory((split / "issue.md").read_text(), issue_number=1)
    rows = {
        "P1": (
            AllocationRowDef("R1", "P1", AllocationKind.OWNED, "Step 1"),
            _stage_row("R1", "P1"),
        ),
        "P2": (AllocationRowDef("R2", "P2", AllocationKind.OWNED, "Step 2"),),
    }

    assert (
        evaluate_coverage(inventory.mode, inventory.requirements, rows).status
        is CoverageStatus.PASS
    )


@pytest.mark.parametrize(
    "mode",
    (InventoryMode.ENUMERATED, InventoryMode.UNENUMERATED, InventoryMode.NO_ISSUE),
)
def test_duplicate_stage_obligation_pair_fails_every_inventory_mode(
    mode: InventoryMode,
) -> None:
    rows = {
        "P1": (_stage_row("P-10", "P1"),),
        "P2": (_stage_row("P-10", "P2"),),
    }

    result = evaluate_coverage(mode, (), rows)

    assert result.status is CoverageStatus.FAIL
    assert result.duplicate == ("P-10",)


def test_assigned_requirements_excludes_stage_rows(tmp_path: Path) -> None:
    inventory = extract_requirement_inventory(
        (_FIXTURES / "split_r1_r2" / "issue.md").read_text(), issue_number=1
    )
    requirement = inventory.requirements[0]
    authority = _enumerated_authority(
        tmp_path,
        (requirement,),
        (_stage_row(requirement.requirement_id, "P1"),),
    )

    assert assigned_requirements(authority, "P1") == ()


def test_stage_obligations_projects_enumerated_and_planner_rows_once_per_pair(
    tmp_path: Path,
) -> None:
    from autoskillit.core import StageObligation, StageObligationDef, stage_obligations

    inventory = extract_requirement_inventory(
        (_FIXTURES / "split_r1_r2" / "issue.md").read_text(), issue_number=1
    )
    requirement_id = inventory.requirements[0].requirement_id
    authority = _enumerated_authority(
        tmp_path,
        (inventory.requirements[0],),
        (
            _stage_row(requirement_id, "P1"),
            _stage_row("P-10", "P1"),
            _stage_row(requirement_id, "P1"),
            _stage_row("P-10", "P1", "pr_left_unmerged"),
            _stage_row("P-10", "P1"),
        ),
    )

    assert stage_obligations(authority) == (
        StageObligationDef(requirement_id, StageObligation.PR_OPENED),
        StageObligationDef("P-10", StageObligation.PR_OPENED),
        StageObligationDef("P-10", StageObligation.PR_LEFT_UNMERGED),
    )


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
