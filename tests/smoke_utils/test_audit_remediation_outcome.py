"""Content-aware audit remediation outcomes."""

from __future__ import annotations

from pathlib import Path

import pytest

from autoskillit.core import (
    ArtifactRef,
    AuditAssessment,
    AuditAssessmentRow,
    AuditCycleAuthority,
    AuditVerdict,
)
from autoskillit.smoke_utils import check_audit_remediation_outcome, merge_audit_cycle_path

pytestmark = [pytest.mark.medium]

_DIGEST = "sha256:" + "a" * 64


def _authority(path: Path, rows: tuple[AuditAssessmentRow, ...]) -> str:
    ref = ArtifactRef(
        locator=str(path.parent / "artifact.md"),
        media_type="text/markdown",
        schema_version=1,
        byte_size=1,
        content_digest=_DIGEST,
    )
    authority = AuditCycleAuthority.create(
        execution_generation="generation-1",
        cycle_id="cycle-1",
        plan_set_id="plans-1",
        scope_id="scope-1",
        part_id="part-1",
        audit_round=1,
        parent_authority_digest=None,
        audited_plan_refs=(ref,),
        inventory_ref=ref,
        assessments=rows,
        verdict=AuditVerdict.NO_GO,
        remediation_ref=ref,
        generated_at="2026-09-19T00:00:00Z",
    )
    path.write_bytes(authority.canonical_bytes)
    return str(path)


def _row(requirement_id: str, assessment: AuditAssessment) -> AuditAssessmentRow:
    return AuditAssessmentRow.create(
        requirement_id=requirement_id,
        requirement_text=f"Requirement {requirement_id}",
        assessment=assessment,
        evidence_summary=f"Evidence for {requirement_id}",
    )


def test_outcomes_compare_blocking_rows_across_rounds(tmp_path: Path) -> None:
    missing = _row("REQ-A", AuditAssessment.MISSING)
    conflict = _row("REQ-B", AuditAssessment.CONFLICT)
    covered = _row("REQ-C", AuditAssessment.COVERED)
    prior = _authority(tmp_path / "prior.json", (missing, conflict, covered))
    repeated = _authority(tmp_path / "reordered.json", (covered, conflict, missing))
    changed = _authority(tmp_path / "changed.json", (missing, covered))
    decision = _authority(
        tmp_path / "decision.json",
        (_row("REQ-D", AuditAssessment.UNSATISFIABLE_BY_CODE), covered),
    )

    def outcome(current: str, *, max_iterations: str = "3", prior_path: str = prior) -> str:
        return check_audit_remediation_outcome(
            current_iteration="0",
            max_iterations=max_iterations,
            current_authority_path=current,
            prior_authority_path=prior_path,
        )["outcome"]

    assert outcome(changed) == "PROGRESSING"
    assert outcome(repeated) == "STUCK_REPEATING"
    assert outcome(changed, max_iterations="1") == "EXHAUSTED"
    assert outcome(decision, max_iterations="1") == "AWAITING_DECISION"
    assert outcome(str(tmp_path / "missing.json")) == "INTEGRITY_FAULT"
    assert outcome(changed, prior_path="") == "PROGRESSING"


def test_integrity_fault_preserves_code_budget(tmp_path: Path) -> None:
    invalid = tmp_path / "invalid.json"
    invalid.write_text('{"assessments": []}', encoding="utf-8")

    assert check_audit_remediation_outcome(
        current_iteration="2",
        max_iterations="3",
        current_authority_path=str(invalid),
    ) == {
        "outcome": "INTEGRITY_FAULT",
        "next_iteration": "2",
        "unresolved_requirement_ids": "",
    }


def test_empty_current_path_preserves_prior_authority() -> None:
    assert merge_audit_cycle_path("", "/trusted/prior.json") == {
        "audit_cycle_path": "/trusted/prior.json"
    }
