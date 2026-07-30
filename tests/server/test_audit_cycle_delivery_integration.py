"""Production-boundary integration tests for parent-owned audit publication."""

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
    AuditCycleVerifier,
    AuditMaterializationStatus,
    AuditReservationRequest,
    AuditSemanticResult,
    AuditVerdict,
    RecipeExecutionId,
    ReservationDecision,
    canonical_json_bytes,
    compute_bytes_hash,
)
from autoskillit.pipeline import DefaultAuditAdmissionLedger
from autoskillit.server._audit_authority_materializer import (
    DefaultAuditAuthorityMaterializer,
)

pytestmark = [pytest.mark.layer("server"), pytest.mark.medium]


def _digest(value: str) -> str:
    return compute_bytes_hash(value.encode("utf-8"))


def _ledger(tmp_path: Path) -> DefaultAuditAdmissionLedger:
    ledger = DefaultAuditAdmissionLedger(
        AuditAdmissionStoreAuthority(
            database_path=(tmp_path / "audit-admission.sqlite3").resolve(),
            expected_owner_id=os.getuid(),
        )
    )
    recovered = ledger.recover_all()
    assert recovered.store_health.status.value == "HEALTHY"
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


def _semantic_path(
    reservation,
    *,
    audited_plan_refs: tuple[ArtifactRef, ...],
) -> Path:
    assessment = AuditAssessmentRow.create(
        requirement_id="REQ-001",
        requirement_text="The parent owns audit authority identity.",
        assessment=AuditAssessment.COVERED,
        evidence_summary="The semantic child output contains no authority identity.",
    )
    semantic = AuditSemanticResult(
        schema_version=AUDIT_SEMANTIC_SCHEMA_VERSION,
        audited_plan_refs=audited_plan_refs,
        assessments=(assessment,),
        verdict=AuditVerdict.GO,
        remediation_ref=None,
    )
    reservation.semantic_result_path.parent.mkdir(parents=True, exist_ok=True)
    reservation.semantic_result_path.write_bytes(canonical_json_bytes(semantic.to_dict()))
    return reservation.semantic_result_path


def test_parent_materializer_injects_server_execution_and_publishes_projection(
    tmp_path: Path,
) -> None:
    ledger = _ledger(tmp_path)
    execution_id = RecipeExecutionId("production-generated-execution")
    installation = ledger.create_or_get_installation(
        recipe_execution_id=execution_id,
        snapshot_digest=_digest("snapshot"),
    )
    plan_ref = _artifact(
        tmp_path / "plan.md",
        b"# Plan\n\nParent owns identity.\n",
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
            cycle_id="cycle-server-owned",
            scope_id="scope-server-owned",
            part_id="part-server-owned",
            allowed_root=tmp_path.resolve(),
        )
    )
    assert reserved.decision is ReservationDecision.DISPATCH_NEW
    assert reserved.reservation is not None

    result = DefaultAuditAuthorityMaterializer(ledger).materialize(
        reservation=reserved.reservation,
        semantic_result_path=_semantic_path(
            reserved.reservation,
            audited_plan_refs=(plan_ref,),
        ),
        preflight_step_names=("consume-plan",),
    )

    assert result.status is AuditMaterializationStatus.PUBLISHED_PENDING_FINALIZATION
    assert result.path is not None
    authority = AuditCycleVerifier(tmp_path).load_authority(result.path)
    assert authority.execution_generation == execution_id.value
    assert authority.cycle_id == "cycle-server-owned"
    head = ledger.current_head(
        recipe_execution_id=execution_id,
        cycle_id=authority.cycle_id,
        scope_id=authority.scope_id,
        part_id=authority.part_id,
    )
    assert head is not None
    assert head.current_authority_digest == authority.authority_digest
    projection = ledger.preflight_projection(
        recipe_execution_id=execution_id,
        installation_version=installation,
        step_name="consume-plan",
    )
    assert projection is not None
    assert (
        projection.plan_set_id,
        projection.scope_id,
        projection.part_id,
    ) == (authority.plan_set_id, authority.scope_id, authority.part_id)


def test_full_reference_substitution_is_rejected_before_authority_write(
    tmp_path: Path,
) -> None:
    ledger = _ledger(tmp_path)
    execution_id = RecipeExecutionId("production-generated-execution")
    installation = ledger.create_or_get_installation(
        recipe_execution_id=execution_id,
        snapshot_digest=_digest("snapshot"),
    )
    plan_ref = _artifact(
        tmp_path / "plan.md",
        b"same bytes",
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
            cycle_id="cycle",
            scope_id="scope",
            part_id="part",
            allowed_root=tmp_path.resolve(),
        )
    )
    assert reserved.reservation is not None
    substituted = ArtifactRef(
        locator=str((tmp_path / "other.md").resolve()),
        media_type=plan_ref.media_type,
        schema_version=plan_ref.schema_version,
        byte_size=plan_ref.byte_size,
        content_digest=plan_ref.content_digest,
    )

    result = DefaultAuditAuthorityMaterializer(ledger).materialize(
        reservation=reserved.reservation,
        semantic_result_path=_semantic_path(
            reserved.reservation,
            audited_plan_refs=(substituted,),
        ),
        preflight_step_names=("consume-plan",),
    )

    assert result.status is AuditMaterializationStatus.SEMANTIC_REJECTED
    assert not reserved.reservation.authority_path.exists()
