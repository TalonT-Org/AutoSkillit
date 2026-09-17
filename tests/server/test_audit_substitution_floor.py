"""Publication-boundary tests for unprescribed audit substitutions."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from autoskillit.core import (
    AUDIT_SEMANTIC_SCHEMA_VERSION,
    ArtifactRef,
    AuditAdmissionStoreAuthority,
    AuditAssessment,
    AuditAssessmentRow,
    AuditIdentityReservation,
    AuditMaterializationResult,
    AuditMaterializationStatus,
    AuditReservationRequest,
    AuditVerdict,
    RecipeExecutionId,
    ReservationDecision,
    canonical_json_bytes,
    compute_bytes_hash,
    compute_canonical_hash,
)
from autoskillit.core.closure_hashing import (
    compute_file_hash,
    compute_report_hash,
    compute_request_hash,
    compute_row_hash,
)
from autoskillit.core.closure_verifier import verify_closure_report
from autoskillit.core.io import write_versioned_json
from autoskillit.core.types import (
    CLOSURE_REPORT_SCHEMA_VERSION,
    ClosureReport,
    ClosureRow,
)
from autoskillit.pipeline import DefaultAuditAdmissionLedger
from autoskillit.server._audit_authority_materializer import (
    DefaultAuditAuthorityMaterializer,
)

pytestmark = [pytest.mark.layer("server"), pytest.mark.medium]

_ROW_DIGEST_DOMAIN = "autoskillit:audit-cycle:assessment-row:v1:sha256"
_INCIDENT_REQUIREMENT = (
    "For a CPU-active child that never stops, child_deferral_ceiling=1.0, "
    "assert kill happens within ~1s of ceiling expiry."
)
_INCIDENT_EVIDENCE = (
    "Uses a persistently-active child (_has_active_child_processes mocked to "
    "always return True) with child_deferral_ceiling=1.0."
)
_LITERAL_EVIDENCE = (
    'Starts subprocess.Popen(["sh", "-c", "sleep 30 & wait"]) and asserts with '
    "psutil that the real child process remains active until termination."
)


def _digest(value: str) -> str:
    return compute_bytes_hash(value.encode("utf-8"))


def _ledger(tmp_path: Path) -> DefaultAuditAdmissionLedger:
    ledger = DefaultAuditAdmissionLedger(
        AuditAdmissionStoreAuthority(
            database_path=(tmp_path / "audit-admission.sqlite3").resolve(),
            expected_owner_id=os.getuid(),
        )
    )
    assert ledger.recover_all().store_health.status.value == "HEALTHY"
    return ledger


def _artifact(path: Path, data: bytes, *, media_type: str) -> ArtifactRef:
    path.write_bytes(data)
    return ArtifactRef(
        locator=str(path.resolve()),
        media_type=media_type,
        schema_version=1,
        byte_size=len(data),
        content_digest=compute_bytes_hash(data),
    )


def _reserve(tmp_path: Path):
    ledger = _ledger(tmp_path)
    execution_id = RecipeExecutionId("substitution-floor-execution")
    installation = ledger.create_or_get_installation(
        recipe_execution_id=execution_id,
        snapshot_digest=_digest("snapshot"),
    )
    plan_ref = _artifact(
        tmp_path / "plan.md",
        b"# Plan\n\nAudit the implementation.\n",
        media_type="text/markdown",
    )
    reserved = ledger.reserve(
        AuditReservationRequest(
            recipe_execution_id=execution_id,
            installation_version=installation,
            step_name="audit",
            invocation_template_digest=_digest("template"),
            slot_intent_digest=_digest("intent"),
            runtime_binding_digest=_digest("runtime"),
            audited_plan_refs=(plan_ref,),
            cycle_id="cycle-substitution",
            scope_id="scope-substitution",
            part_id="part-substitution",
            allowed_root=tmp_path.resolve(),
        )
    )
    assert reserved.decision is ReservationDecision.DISPATCH_NEW
    assert reserved.reservation is not None
    return ledger, execution_id, plan_ref, reserved.reservation


def _row(
    assessment: AuditAssessment,
    *,
    requirement_text: str = _INCIDENT_REQUIREMENT,
    evidence_summary: str = _INCIDENT_EVIDENCE,
) -> AuditAssessmentRow:
    if assessment is not AuditAssessment.NAMED_DEVIATION:
        return AuditAssessmentRow.create(
            requirement_id="REQ-006",
            requirement_text=requirement_text,
            assessment=assessment,
            evidence_summary=evidence_summary,
        )
    payload = {
        "assessment": assessment.value,
        "evidence_summary": evidence_summary,
        "requirement_id": "REQ-006",
        "requirement_text": requirement_text,
    }
    return AuditAssessmentRow.from_dict(
        {
            **payload,
            "row_digest": compute_canonical_hash(payload, domain=_ROW_DIGEST_DOMAIN),
        }
    )


def _materialize(
    tmp_path: Path,
    *,
    assessment: AuditAssessment,
    verdict: AuditVerdict,
    evidence_summary: str = _INCIDENT_EVIDENCE,
) -> tuple[
    AuditMaterializationResult,
    tuple[DefaultAuditAdmissionLedger, AuditIdentityReservation],
    RecipeExecutionId,
]:
    ledger, execution_id, plan_ref, reservation = _reserve(tmp_path)
    remediation_ref = None
    if verdict is AuditVerdict.NO_GO:
        remediation_ref = _artifact(
            tmp_path / "remediation.md",
            b"# Remediation\n",
            media_type="text/markdown",
        )
    row = _row(assessment, evidence_summary=evidence_summary)
    semantic_payload = {
        "schema_version": AUDIT_SEMANTIC_SCHEMA_VERSION,
        "audited_plan_refs": [plan_ref.to_dict()],
        "assessments": [row.to_dict()],
        "verdict": verdict.value,
        "remediation_ref": remediation_ref.to_dict() if remediation_ref is not None else None,
    }
    reservation.semantic_result_path.parent.mkdir(parents=True, exist_ok=True)
    reservation.semantic_result_path.write_bytes(canonical_json_bytes(semantic_payload))
    result = DefaultAuditAuthorityMaterializer(ledger).materialize(
        reservation=reservation,
        semantic_result_path=reservation.semantic_result_path,
        preflight_step_names=("audit-preflight",),
    )
    return result, (ledger, reservation), execution_id


def _assert_no_authority_was_committed(
    ledger: DefaultAuditAdmissionLedger,
    reservation: AuditIdentityReservation,
    execution_id: RecipeExecutionId,
) -> None:
    assert not reservation.authority_path.exists()
    assert (
        ledger.current_head(
            recipe_execution_id=execution_id,
            cycle_id=reservation.cycle_id,
            scope_id=reservation.scope_id,
            part_id=reservation.part_id,
        )
        is None
    )


def test_covered_substitution_is_rejected_before_authority_creation(tmp_path: Path) -> None:
    result, context, execution_id = _materialize(
        tmp_path,
        assessment=AuditAssessment.COVERED,
        verdict=AuditVerdict.GO,
    )
    ledger, reservation = context

    assert result.status is AuditMaterializationStatus.SEMANTIC_REJECTED
    assert result.error is not None and "REQ-006" in result.error
    _assert_no_authority_was_committed(ledger, reservation, execution_id)


def test_blocking_unprescribed_substitution_with_no_go_materializes(tmp_path: Path) -> None:
    result, context, _execution_id = _materialize(
        tmp_path,
        assessment=AuditAssessment.UNPRESCRIBED_SUBSTITUTION,
        verdict=AuditVerdict.NO_GO,
    )
    _ledger_value, reservation = context

    assert result.status is AuditMaterializationStatus.PUBLISHED_PENDING_FINALIZATION
    assert result.path == reservation.authority_path
    assert reservation.authority_path.exists()


def test_go_with_blocking_substitution_is_rejected_by_existing_invariant(tmp_path: Path) -> None:
    result, context, execution_id = _materialize(
        tmp_path,
        assessment=AuditAssessment.UNPRESCRIBED_SUBSTITUTION,
        verdict=AuditVerdict.GO,
    )
    ledger, reservation = context

    assert result.status is AuditMaterializationStatus.SEMANTIC_REJECTED
    _assert_no_authority_was_committed(ledger, reservation, execution_id)


def test_literal_implementation_remains_covered(tmp_path: Path) -> None:
    result, context, _execution_id = _materialize(
        tmp_path,
        assessment=AuditAssessment.COVERED,
        verdict=AuditVerdict.GO,
        evidence_summary=_LITERAL_EVIDENCE,
    )
    _ledger_value, reservation = context

    assert result.status is AuditMaterializationStatus.PUBLISHED_PENDING_FINALIZATION
    assert result.path == reservation.authority_path


def test_named_deviation_is_rejected_at_the_submission_boundary(tmp_path: Path) -> None:
    result, context, execution_id = _materialize(
        tmp_path,
        assessment=AuditAssessment.NAMED_DEVIATION,
        verdict=AuditVerdict.GO,
        evidence_summary="A historical label was submitted without resolution.",
    )
    ledger, reservation = context

    assert result.status is AuditMaterializationStatus.SEMANTIC_REJECTED
    assert result.error is not None and "NAMED_DEVIATION" in result.error
    _assert_no_authority_was_committed(ledger, reservation, execution_id)


def _closure_result(tmp_path: Path, *, assessment: str, verdict: str):
    authority_path = tmp_path / "authority.json"
    authority_path.write_bytes(b"authority")
    authority_hash = compute_file_hash(authority_path)
    row = ClosureRow(
        requirement_id="REQ-006",
        requirement_text=_INCIDENT_REQUIREMENT,
        source_file="tests/execution/test_termination_executor.py",
        source_line=29,
        source_section="Child process deferral",
        assessment=assessment,
        evidence_summary=_INCIDENT_EVIDENCE,
        row_hash=compute_row_hash(
            "REQ-006",
            _INCIDENT_REQUIREMENT,
            assessment,
            _INCIDENT_EVIDENCE,
            "tests/execution/test_termination_executor.py",
            29,
            "Child process deferral",
        ),
    )
    request_hash = compute_request_hash(authority_hash, [], "main", "diff", "target")
    report = ClosureReport(
        schema_version=CLOSURE_REPORT_SCHEMA_VERSION,
        request_hash=request_hash,
        authority_hash=authority_hash,
        plan_hashes=(),
        base_sha="main",
        diff_sha="diff",
        target_sha="target",
        requirement_ids=(row.requirement_id,),
        rows=(row,),
        verdict=verdict,
        report_hash=compute_report_hash(request_hash, [row.row_hash], verdict),
        remediation_path="/tmp/remediation.md" if verdict == "NO GO" else None,
        generated_at="2026-09-16T00:00:00Z",
    )
    output_root = tmp_path / "closure"
    output_root.mkdir()
    report_path = output_root / "closure_report.json"
    write_versioned_json(
        report_path,
        report.to_dict(),
        schema_version=CLOSURE_REPORT_SCHEMA_VERSION,
    )
    return verify_closure_report(
        report_path=report_path,
        authority_path=authority_path,
        authority_hash=authority_hash,
        output_root=output_root,
        plan_paths=(),
        base_sha="main",
        diff_sha="diff",
        target_sha="target",
    )


def test_closure_verifier_rejects_a_covered_incident_substitution(tmp_path: Path) -> None:
    result = _closure_result(tmp_path, assessment="COVERED", verdict="GO")

    assert result.success is False
    assert any("REQ-006" in error for error in result.errors)


def test_closure_verifier_accepts_a_blocking_substitution_with_no_go(tmp_path: Path) -> None:
    result = _closure_result(
        tmp_path,
        assessment="UNPRESCRIBED_SUBSTITUTION",
        verdict="NO GO",
    )

    assert result.success is True
    assert result.verdict == "NO GO"
