"""Executable inventory-admission truth table."""

from __future__ import annotations

from pathlib import Path

import pytest

from autoskillit.core import (
    AdmissionReason,
    AdmissionStatus,
    ArtifactRef,
    AuditAssessment,
    AuditAssessmentRow,
    AuditCycleAuthority,
    AuditCycleHead,
    AuditCycleVerifier,
    AuditVerdict,
    InventoryAdmissionDecision,
    InventoryAdmissionEvaluator,
    PlanDispositionReport,
    PlanDispositionRow,
)
from autoskillit.core.closure_hashing import compute_bytes_hash

pytestmark = [pytest.mark.layer("core"), pytest.mark.small]

_HASH_A = "sha256:" + "a" * 64
_HASH_B = "sha256:" + "b" * 64


def _ref(path: Path, digest: str = _HASH_A, size: int = 10) -> ArtifactRef:
    return ArtifactRef(
        locator=str(path),
        media_type="application/json",
        schema_version=1,
        byte_size=size,
        content_digest=digest,
    )


def _assessment(requirement_id: str, assessment: AuditAssessment) -> AuditAssessmentRow:
    return AuditAssessmentRow.create(
        requirement_id=requirement_id,
        requirement_text=f"text for {requirement_id}",
        assessment=assessment,
        evidence_summary=f"evidence for {requirement_id}",
    )


def _authority(
    tmp_path: Path,
    *,
    verdict: AuditVerdict = AuditVerdict.NO_GO,
    rows: tuple[AuditAssessmentRow, ...] | None = None,
) -> AuditCycleAuthority:
    if rows is None:
        rows = (
            _assessment("ITEM-A", AuditAssessment.COVERED),
            _assessment("ITEM-B", AuditAssessment.MISSING),
        )
    return AuditCycleAuthority.create(
        execution_generation="generation-1",
        cycle_id="cycle-1",
        plan_set_id="plans-1",
        scope_id="scope-1",
        part_id="part-a",
        audit_round=2,
        parent_authority_digest=_HASH_A,
        audited_plan_refs=(_ref(tmp_path / "audited-plan.md"),),
        inventory_ref=_ref(tmp_path / "inventory.json", _HASH_B),
        assessments=rows,
        verdict=verdict,
        remediation_ref=(
            _ref(tmp_path / "remediation.md") if verdict is AuditVerdict.NO_GO else None
        ),
        generated_at="2026-07-23T00:00:00Z",
    )


def _head(authority: AuditCycleAuthority, *, successor: str | None = None) -> AuditCycleHead:
    return AuditCycleHead(
        execution_generation=authority.execution_generation,
        cycle_id=authority.cycle_id,
        plan_set_id=authority.plan_set_id,
        scope_id=authority.scope_id,
        part_id=authority.part_id,
        current_authority_digest=authority.authority_digest,
        audit_round=authority.audit_round,
        verdict=authority.verdict,
        authorized_successor_part_id=successor,
    )


def _dispositions(*, carry_step: str = "Step 2.1") -> tuple[PlanDispositionRow, ...]:
    return (
        PlanDispositionRow.create(
            requirement_id="ITEM-A",
            disposition="satisfied-by-round-2",
        ),
        PlanDispositionRow.create(
            requirement_id="ITEM-B",
            disposition="carried@step",
            implementation_step=carry_step,
        ),
    )


def _report(
    tmp_path: Path,
    authority: AuditCycleAuthority,
    *,
    rows: tuple[PlanDispositionRow, ...] | None = None,
    cycle_id: str | None = None,
    generation: str | None = None,
    scope_id: str | None = None,
    part_id: str | None = None,
    inventory_digest: str | None = None,
    findings_digest: str | None = None,
) -> PlanDispositionReport:
    if rows is None:
        rows = _dispositions()
    plan_text = _plan_text(rows)
    plan_digest = compute_bytes_hash(plan_text.encode())
    return PlanDispositionReport.create(
        execution_generation=generation or authority.execution_generation,
        cycle_id=cycle_id or authority.cycle_id,
        plan_set_id=authority.plan_set_id,
        scope_id=scope_id or authority.scope_id,
        part_id=part_id or authority.part_id,
        audit_round=authority.audit_round,
        parent_authority_digest=authority.authority_digest,
        inventory_digest=inventory_digest or authority.inventory_ref.content_digest,
        findings_digest=findings_digest or authority.findings_digest,
        current_plan_ref=_ref(
            tmp_path / "current-plan.md",
            plan_digest,
            len(plan_text.encode()),
        ),
        dispositions=rows,
        generated_at="2026-07-23T00:01:00Z",
    )


def _plan_text(
    rows: tuple[PlanDispositionRow, ...],
    *,
    include_step: bool = True,
) -> str:
    table_rows = "\n".join(
        f"| {row.requirement_id} | {row.disposition} | {row.implementation_step or '—'} |"
        for row in rows
    )
    steps = (
        "\n## Implementation Steps\n\n"
        "### Step 2.1: Implement blocking requirement\n\n"
        "Implement ITEM-B in the current remediation.\n"
        if include_step
        else ""
    )
    return (
        "# Plan\n\n"
        "## Requirements Map\n\n"
        "| Requirement ID | Disposition | Implementation Step |\n"
        "|---|---|---|\n"
        f"{table_rows}\n"
        f"{steps}"
    )


def _evaluate(
    authority: AuditCycleAuthority | None,
    report: PlanDispositionReport | None,
    *,
    head: AuditCycleHead | None = None,
    inventory_ids: tuple[str, ...] = ("ITEM-A", "ITEM-B"),
    plan_text: str | None = None,
    expected_part: str = "part-a",
) -> InventoryAdmissionDecision:
    if authority is not None and head is None:
        head = _head(authority)
    return InventoryAdmissionEvaluator().evaluate(
        authority=authority,
        trusted_head=head,
        report=report,
        expected_generation="generation-1",
        expected_plan_set_id="plans-1",
        expected_scope_id="scope-1",
        expected_part_id=expected_part,
        current_plan_ref=report.current_plan_ref if report is not None else None,
        inventory_requirement_ids=inventory_ids,
        current_plan_text=plan_text or (_plan_text(report.dispositions) if report else ""),
    )


def test_absent_authority_and_report_omits() -> None:
    decision = _evaluate(None, None)
    assert decision.status is AdmissionStatus.OMIT
    assert decision.reason is AdmissionReason.NO_AUTHORITY


def test_report_without_authority_rejects(tmp_path: Path) -> None:
    authority = _authority(tmp_path)
    decision = _evaluate(None, _report(tmp_path, authority))
    assert decision.status is AdmissionStatus.REJECT
    assert decision.reason is AdmissionReason.REPORT_WITHOUT_AUTHORITY


def test_complete_two_disposition_truth_table_passes(tmp_path: Path) -> None:
    authority = _authority(tmp_path)
    report = _report(tmp_path, authority)
    plan = _plan_text(report.dispositions)
    unchanged = plan[:]
    decision = _evaluate(authority, report, plan_text=plan)
    assert decision.status is AdmissionStatus.PASS
    assert decision.reason is AdmissionReason.ADMITTED
    assert [row.disposition for row in decision.dispositions] == [
        "satisfied-by-round-2",
        "carried@step",
    ]
    assert plan == unchanged


def test_blocking_requirement_without_real_step_rejects(tmp_path: Path) -> None:
    authority = _authority(tmp_path)
    report = _report(tmp_path, authority)
    decision = _evaluate(
        authority,
        report,
        plan_text=_plan_text(report.dispositions, include_step=False),
    )
    assert decision.status is AdmissionStatus.REJECT
    assert decision.reason is AdmissionReason.IMPLEMENTATION_STEP_MISSING


def test_authoritative_empty_findings_satisfies_every_row(tmp_path: Path) -> None:
    authority = _authority(
        tmp_path,
        rows=(
            _assessment("ITEM-A", AuditAssessment.COVERED),
            _assessment("ITEM-B", AuditAssessment.COVERED),
        ),
    )
    rows = (
        PlanDispositionRow.create(requirement_id="ITEM-A", disposition="satisfied-by-round-2"),
        PlanDispositionRow.create(requirement_id="ITEM-B", disposition="satisfied-by-round-2"),
    )
    report = _report(tmp_path, authority, rows=rows)
    decision = _evaluate(authority, report, plan_text=_plan_text(rows, include_step=False))
    assert decision.status is AdmissionStatus.PASS


@pytest.mark.parametrize(
    ("override", "reason"),
    [
        ({"cycle_id": "cycle-other"}, AdmissionReason.CYCLE_MISMATCH),
        ({"generation": "generation-other"}, AdmissionReason.GENERATION_MISMATCH),
        ({"scope_id": "scope-other"}, AdmissionReason.SCOPE_MISMATCH),
        ({"part_id": "part-b"}, AdmissionReason.PART_MISMATCH),
        ({"inventory_digest": _HASH_A}, AdmissionReason.INVENTORY_MISMATCH),
        (
            {"findings_digest": "sha256:" + "c" * 64},
            AdmissionReason.FINDINGS_MISMATCH,
        ),
    ],
)
def test_active_no_go_identity_mismatches_reject_before_comparison(
    tmp_path: Path,
    override: dict[str, str],
    reason: AdmissionReason,
) -> None:
    authority = _authority(tmp_path)
    report = _report(tmp_path, authority, **override)
    decision = _evaluate(authority, report)
    assert decision.status is AdmissionStatus.REJECT
    assert decision.reason is reason


@pytest.mark.parametrize(
    "ids",
    [
        ("ITEM-B", "ITEM-A"),
        ("ITEM-A",),
        ("ITEM-A", "ITEM-B", "ITEM-C"),
        ("ITEM-A", "ITEM-A"),
    ],
)
def test_inventory_order_duplicates_missing_and_extra_reject(
    tmp_path: Path, ids: tuple[str, ...]
) -> None:
    authority = _authority(tmp_path)
    report = _report(tmp_path, authority)
    decision = _evaluate(authority, report, inventory_ids=ids)
    assert decision.status is AdmissionStatus.REJECT
    assert decision.reason in {
        AdmissionReason.INVENTORY_INVALID,
        AdmissionReason.REQUIREMENT_ORDER_MISMATCH,
    }


def test_report_row_reorder_rejects(tmp_path: Path) -> None:
    authority = _authority(tmp_path)
    rows = tuple(reversed(_dispositions()))
    report = _report(tmp_path, authority, rows=rows)
    decision = _evaluate(authority, report, plan_text=_plan_text(rows))
    assert decision.reason is AdmissionReason.REQUIREMENT_ORDER_MISMATCH


def test_satisfied_row_must_name_current_audit_round(tmp_path: Path) -> None:
    authority = _authority(tmp_path)
    rows = (
        PlanDispositionRow.create(requirement_id="ITEM-A", disposition="satisfied-by-round-1"),
        _dispositions()[1],
    )
    report = _report(tmp_path, authority, rows=rows)
    decision = _evaluate(authority, report, plan_text=_plan_text(rows))
    assert decision.reason is AdmissionReason.SATISFIED_ROUND_MISMATCH


def test_report_without_authority_rejects_without_read(tmp_path: Path) -> None:
    reads: list[Path] = []

    def reader(
        path: str | Path,
        root: str | Path,
        *,
        max_size_bytes: int,
    ) -> tuple[Path, bytes]:
        del root, max_size_bytes
        reads.append(Path(path))
        raise AssertionError(f"unexpected report read: {path}")

    decision = AuditCycleVerifier(tmp_path, reader=reader).evaluate_paths(
        authority_path=None,
        report_path=tmp_path.parent / "escaped-report.json",
        trusted_head=None,
        current_plan_path=tmp_path / "plan.md",
        expected_generation="generation-1",
        expected_plan_set_id="plans-1",
        expected_scope_id="scope-1",
        expected_part_id="part-a",
    )
    assert decision.status is AdmissionStatus.REJECT
    assert decision.reason is AdmissionReason.REPORT_WITHOUT_AUTHORITY
    assert reads == []


def test_trusted_go_successor_omits_without_inventory_read(tmp_path: Path) -> None:
    authority = _authority(
        tmp_path,
        verdict=AuditVerdict.GO,
        rows=(
            _assessment("ITEM-A", AuditAssessment.COVERED),
            _assessment("ITEM-B", AuditAssessment.COVERED),
        ),
    )
    authority_path = tmp_path / "authority.json"
    reads: list[Path] = []

    def reader(
        path: str | Path,
        root: str | Path,
        *,
        max_size_bytes: int,
    ) -> tuple[Path, bytes]:
        del root, max_size_bytes
        reads.append(Path(path))
        if Path(path) == authority_path:
            return authority_path, authority.canonical_bytes
        raise AssertionError(f"unexpected inventory read: {path}")

    decision = AuditCycleVerifier(tmp_path, reader=reader).evaluate_paths(
        authority_path=authority_path,
        report_path=None,
        trusted_head=_head(authority, successor="part-b"),
        current_plan_path=tmp_path / "part-b.md",
        expected_generation=authority.execution_generation,
        expected_plan_set_id=authority.plan_set_id,
        expected_scope_id=authority.scope_id,
        expected_part_id="part-b",
    )
    assert decision.status is AdmissionStatus.OMIT
    assert decision.reason is AdmissionReason.TRUSTED_GO_SUCCESSOR
    assert reads == [authority_path]


def test_stale_no_go_authority_rejects(tmp_path: Path) -> None:
    authority = _authority(tmp_path)
    report = _report(tmp_path, authority)
    stale_head = AuditCycleHead(
        execution_generation=authority.execution_generation,
        cycle_id=authority.cycle_id,
        plan_set_id=authority.plan_set_id,
        scope_id=authority.scope_id,
        part_id=authority.part_id,
        current_authority_digest="sha256:" + "d" * 64,
        audit_round=authority.audit_round,
        verdict=authority.verdict,
    )
    decision = _evaluate(authority, report, head=stale_head)
    assert decision.reason is AdmissionReason.AUTHORITY_NOT_CURRENT
