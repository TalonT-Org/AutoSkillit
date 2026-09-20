"""A human decision waiver remains bound to one finding across audit rounds."""

from __future__ import annotations

from dataclasses import replace
from datetime import date
from pathlib import Path

import pytest

from autoskillit.core import (
    AdmissionReason,
    AdmissionStatus,
    AuditAssessment,
    AuditCycleAuthority,
    AuditFindingWaiver,
    InventoryAdmissionEvaluator,
    PlanDispositionRow,
)
from tests.core.test_inventory_admission import (
    _assessment,
    _authority,
    _head,
    _plan_text,
    _report,
)

pytestmark = [pytest.mark.layer("core"), pytest.mark.small]


def _decision(authority: AuditCycleAuthority, report, waivers: tuple[AuditFindingWaiver, ...]):
    return InventoryAdmissionEvaluator().evaluate(
        authority=authority,
        trusted_head=_head(authority),
        report=report,
        expected_generation=authority.execution_generation,
        expected_plan_set_id=authority.plan_set_id,
        expected_scope_id=authority.scope_id,
        expected_part_id=authority.part_id,
        current_plan_ref=report.current_plan_ref,
        inventory_requirement_ids=("ITEM-B",),
        current_plan_text=_plan_text(report.dispositions),
        waivers=waivers,
    )


def test_decision_waiver_survives_a_successor_round(tmp_path: Path) -> None:
    row = _assessment("ITEM-B", AuditAssessment.UNSATISFIABLE_BY_CODE)
    first = _authority(tmp_path, rows=(row,))
    disposition = PlanDispositionRow.create(
        requirement_id="ITEM-B", disposition="waived-by-decision@decision-1"
    )
    first_report = _report(tmp_path, first, rows=(disposition,))
    waiver = AuditFindingWaiver(
        waiver_id="decision-1",
        requirement_id="ITEM-B",
        finding_row_digest=row.row_digest,
        plan_set_id=first.plan_set_id,
        scope_id=first.scope_id,
        part_id=first.part_id,
        rationale="The requirement needs a recorded human decision.",
        issue=4313,
        approved_by="reviewer",
        added_date=date.today(),
        review_date=date.today(),
    )

    assert _decision(first, first_report, (waiver,)).status is AdmissionStatus.PASS
    assert _decision(first, first_report, ()).reason is AdmissionReason.WAIVER_NOT_FOUND
    assert _decision(first, first_report, (replace(waiver, part_id="part-b"),)).reason is (
        AdmissionReason.WAIVER_PART_MISMATCH
    )
    assert _decision(
        first, first_report, (replace(waiver, finding_row_digest="sha256:" + "f" * 64),)
    ).reason is (AdmissionReason.WAIVER_DIGEST_MISMATCH)

    successor = AuditCycleAuthority.create(
        execution_generation=first.execution_generation,
        cycle_id=first.cycle_id,
        plan_set_id=first.plan_set_id,
        scope_id=first.scope_id,
        part_id=first.part_id,
        audit_round=first.audit_round + 1,
        parent_authority_digest=first.authority_digest,
        audited_plan_refs=first.audited_plan_refs,
        inventory_ref=first.inventory_ref,
        assessments=(row,),
        verdict=first.verdict,
        remediation_ref=first.remediation_ref,
        generated_at="2026-09-19T00:00:00Z",
    )
    successor_report = _report(tmp_path, successor, rows=(disposition,))
    assert _decision(successor, successor_report, (waiver,)).status is AdmissionStatus.PASS


def test_code_fixable_finding_cannot_use_decision_waiver(tmp_path: Path) -> None:
    authority = _authority(tmp_path, rows=(_assessment("ITEM-B", AuditAssessment.MISSING),))
    report = _report(
        tmp_path,
        authority,
        rows=(
            PlanDispositionRow.create(
                requirement_id="ITEM-B", disposition="waived-by-decision@decision-1"
            ),
        ),
    )

    assert _decision(authority, report, ()).reason is AdmissionReason.WAIVER_NOT_APPLICABLE
