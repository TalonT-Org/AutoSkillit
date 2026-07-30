"""Production-boundary integration tests for parent-owned audit publication."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from autoskillit.core import (
    AUDIT_SEMANTIC_SCHEMA_VERSION,
    ArtifactRef,
    AuditAdmissionStoreAuthority,
    AuditAssessment,
    AuditAssessmentRow,
    AuditCycleVerificationError,
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
from autoskillit.server import _recipe_execution
from autoskillit.server._audit_authority_materializer import (
    DefaultAuditAuthorityMaterializer,
)
from autoskillit.server.tools.tools_audit_artifacts import (
    write_standalone_audit_evidence_sync,
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


def test_prompt_contract_mode_is_bound_before_child_dispatch() -> None:
    standalone = _recipe_execution.build_standalone_child_prompt(
        "/autoskillit:audit-impl",
        "/tmp",
        None,
        audit_output_mode=_recipe_execution.AuditOutputMode.STANDALONE,
    )
    standalone_payload = json.loads(standalone.split("AUTOSKILLIT_BOUND_INVOCATION_V1\n", 1)[1])
    assert standalone_payload["audit_output_mode"] == "standalone"
    assert "audit_semantic_submission" not in standalone_payload

    attested = _recipe_execution.build_bound_child_prompt(
        "/autoskillit:audit-impl",
        (),
        None,
        audit_reservation_handle="opaque-handle",
        audit_output_mode=_recipe_execution.AuditOutputMode.ATTESTED,
    )
    attested_payload = json.loads(attested.split("AUTOSKILLIT_BOUND_INVOCATION_V1\n", 1)[1])
    assert attested_payload["audit_output_mode"] == "attested"
    assert attested_payload["audit_semantic_submission"]["reservation_handle"] == ("opaque-handle")


def test_standalone_evidence_is_not_loadable_as_authority(tmp_path: Path) -> None:
    plan_ref = _artifact(
        tmp_path / "standalone-plan.md",
        b"standalone plan",
        media_type="text/markdown",
    )
    result = write_standalone_audit_evidence_sync(
        temp_root=tmp_path,
        audited_plan_refs=[plan_ref.to_dict()],
        assessments=[
            {
                "requirement_id": "REQ-001",
                "requirement_text": "Standalone evidence stays non-published.",
                "assessment": "COVERED",
                "evidence_summary": "The artifact carries the standalone schema kind.",
            }
        ],
        verdict="GO",
        remediation_ref=None,
    )

    assert result["audit_status"] == "NON_PUBLISHED_STANDALONE"
    with pytest.raises(AuditCycleVerificationError):
        AuditCycleVerifier(tmp_path).load_authority(result["standalone_evidence_path"])


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
