"""Decision-required findings remain blocking audit evidence."""

from __future__ import annotations

from pathlib import Path

import pytest

from autoskillit.core import (
    AuditAssessment,
    AuditAssessmentRow,
    AuditCycleAuthority,
    AuditDisposition,
    AuditVerdict,
    compute_findings_digest,
)
from tests.core.test_inventory_admission import _ref

pytestmark = [pytest.mark.layer("core"), pytest.mark.small]


def test_decision_required_row_is_persistable_and_blocking(tmp_path: Path) -> None:
    row = AuditAssessmentRow.create(
        requirement_id="REQ-DECISION",
        requirement_text="This unresolved requirement needs a human decision.",
        assessment=AuditAssessment.UNSATISFIABLE_BY_CODE,
        evidence_summary="The planned mechanism cannot be implemented in this repository.",
    )
    assert row.assessment.disposition is AuditDisposition.REQUIRES_DECISION
    assert row.assessment.blocking is True
    assert compute_findings_digest((row,)) != compute_findings_digest(())

    with pytest.raises(ValueError, match="GO.*blocking"):
        AuditCycleAuthority.create(
            execution_generation="generation-1",
            cycle_id="cycle-1",
            plan_set_id="plans-1",
            scope_id="scope-1",
            part_id="part-1",
            audit_round=1,
            parent_authority_digest=None,
            audited_plan_refs=(_ref(tmp_path / "plan.md"),),
            inventory_ref=_ref(tmp_path / "inventory.json"),
            assessments=(row,),
            verdict=AuditVerdict.GO,
            remediation_ref=None,
            generated_at="2026-09-19T00:00:00Z",
        )
