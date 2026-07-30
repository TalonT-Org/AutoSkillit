"""Dormant audit-admission-ledger value-contract tests."""

from __future__ import annotations

import hashlib
import inspect
from pathlib import Path

import pytest

from autoskillit.core.types._type_audit_admission import (
    AuditAttemptId,
    AuditIdentityReservation,
    AuditOutcome,
    AuditOutcomeStatus,
    AuditPreparedEffect,
    AuditPreparedEffectDeliveryStatus,
    AuditRound,
    AuditSlotKey,
    InstallationVersion,
    RecipeExecutionId,
    ReservationDecision,
    compute_audit_reference_identity,
    compute_audit_slot_id,
)
from autoskillit.core.types._type_audit_admission_ledger import (
    AuditAdmissionLedger,
    AuditAdmissionRecoveryResult,
    AuditAdmissionStorageError,
    AuditAdmissionStorageFailureReason,
    AuditAdmissionStorageHealthStatus,
    AuditAdmissionStoreAuthority,
    AuditAdmissionStoreHealth,
    AuditDispositionCommitOutcome,
    AuditDispositionCommitRequest,
    AuditFinalCommitOutcome,
    AuditFinalCommitRequest,
    AuditPreflightProjection,
    AuditPrepareOutcome,
    AuditPrepareRequest,
    AuditReservationOutcome,
    AuditReservationRequest,
)
from autoskillit.core.types._type_audit_cycle import ArtifactRef, AuditCycleHead, AuditVerdict

pytestmark = [pytest.mark.layer("core"), pytest.mark.small]


def _digest(tag: str) -> str:
    return "sha256:" + hashlib.sha256(tag.encode()).hexdigest()


def test_finalization_effect_protocol_api_is_attempt_keyed() -> None:
    read_signature = inspect.signature(AuditAdmissionLedger.finalization_effect_result)
    acknowledge_signature = inspect.signature(AuditAdmissionLedger.acknowledge_finalization_effect)

    assert tuple(read_signature.parameters) == ("self", "attempt_id", "effect_name")
    assert tuple(acknowledge_signature.parameters) == (
        "self",
        "attempt_id",
        "effect_name",
        "result",
    )


def _ref(tag: str = "ref") -> ArtifactRef:
    return ArtifactRef(
        locator=f"/tmp/{tag}.md",
        media_type="text/markdown",
        schema_version=1,
        byte_size=1,
        content_digest=_digest("a"),
    )


def _slot_key() -> AuditSlotKey:
    return AuditSlotKey(
        recipe_execution_id=RecipeExecutionId("exec-1"),
        installation_version=InstallationVersion("install-1"),
        step_name="audit-impl",
        invocation_template_digest=_digest("t"),
        slot_intent_digest=_digest("i"),
        ordered_reference_identity=compute_audit_reference_identity((_ref(),)),
        prior_authority_digest=None,
    )


def _head() -> AuditCycleHead:
    ref = _ref()
    return AuditCycleHead(
        execution_generation="exec-1",
        cycle_id="cycle-1",
        plan_set_id="plan-set-1",
        scope_id="scope-1",
        part_id="part-1",
        current_authority_digest=_digest("h"),
        audit_round=1,
        audited_plan_refs=(ref,),
        inventory_ref=ref,
        verdict=AuditVerdict.GO,
    )


def _reservation() -> AuditIdentityReservation:
    slot_key = _slot_key()
    root = Path("/tmp/audit-admission")
    return AuditIdentityReservation(
        slot_id=compute_audit_slot_id(slot_key),
        slot_key=slot_key,
        current_attempt_id=AuditAttemptId("attempt-1"),
        runtime_binding_digest=_digest("rb"),
        reference_identity_profile_id="ordered-full-reference-v1",
        audited_plan_refs=(_ref(),),
        plan_set_id=slot_key.ordered_reference_identity,
        cycle_id="cycle-1",
        scope_id="scope-1",
        part_id="part-1",
        audit_round=AuditRound(1),
        parent_authority_digest=None,
        generated_at="2026-01-01T00:00:00Z",
        allowed_root=root,
        semantic_result_path=root / "semantic.json",
        inventory_path=root / "inventory.json",
        authority_path=root / "authority.json",
        expected_head=None,
    )


class TestAuditAdmissionStoreAuthority:
    def test_requires_absolute_named_path(self) -> None:
        with pytest.raises(ValueError, match="invalid_audit_admission_store_path"):
            AuditAdmissionStoreAuthority(database_path=Path("relative.db"), expected_owner_id=0)

    def test_requires_non_negative_non_bool_owner(self) -> None:
        with pytest.raises(ValueError, match="invalid_audit_admission_store_owner"):
            AuditAdmissionStoreAuthority(
                database_path=Path("/tmp/ledger.db"), expected_owner_id=-1
            )
        with pytest.raises(ValueError, match="invalid_audit_admission_store_owner"):
            AuditAdmissionStoreAuthority(
                database_path=Path("/tmp/ledger.db"),
                expected_owner_id=True,  # type: ignore[arg-type]
            )

    def test_accepts_valid_authority(self) -> None:
        authority = AuditAdmissionStoreAuthority(
            database_path=Path("/tmp/audit-admission/ledger.db"),
            expected_owner_id=0,
        )
        assert authority.expected_owner_id == 0


class TestAuditAdmissionStoreHealth:
    def test_healthy_cannot_carry_failure_reason(self) -> None:
        with pytest.raises(ValueError, match="only FAIL_CLOSED"):
            AuditAdmissionStoreHealth(
                status=AuditAdmissionStorageHealthStatus.HEALTHY,
                failure_reason=AuditAdmissionStorageFailureReason.IO,
            )

    def test_fail_closed_requires_reason_and_code(self) -> None:
        with pytest.raises(ValueError, match="requires failure_reason"):
            AuditAdmissionStoreHealth(status=AuditAdmissionStorageHealthStatus.FAIL_CLOSED)
        with pytest.raises(ValueError, match="reason_code"):
            AuditAdmissionStoreHealth(
                status=AuditAdmissionStorageHealthStatus.FAIL_CLOSED,
                failure_reason=AuditAdmissionStorageFailureReason.IO,
            )

    def test_fail_closed_accepts_reason_and_code(self) -> None:
        health = AuditAdmissionStoreHealth(
            status=AuditAdmissionStorageHealthStatus.FAIL_CLOSED,
            failure_reason=AuditAdmissionStorageFailureReason.INTEGRITY,
            reason_code="corrupt-metadata",
        )
        assert health.status is AuditAdmissionStorageHealthStatus.FAIL_CLOSED


class TestAuditAdmissionStorageError:
    def test_carries_reason_and_code(self) -> None:
        error = AuditAdmissionStorageError(AuditAdmissionStorageFailureReason.IO, "boom")
        assert error.reason is AuditAdmissionStorageFailureReason.IO
        assert error.reason_code == "boom"
        assert "boom" in str(error)


class TestAuditAdmissionRecoveryResult:
    def test_unhealthy_recovery_cannot_report_records(self) -> None:
        health = AuditAdmissionStoreHealth(
            status=AuditAdmissionStorageHealthStatus.FAIL_CLOSED,
            failure_reason=AuditAdmissionStorageFailureReason.IO,
            reason_code="x",
        )
        with pytest.raises(ValueError, match="only a HEALTHY recovery"):
            AuditAdmissionRecoveryResult(
                store_health=health,
                recovered_installations=(RecipeExecutionId("exec-1"),),
                recovered_attempts=(),
            )

    def test_healthy_recovery_accepts_records(self) -> None:
        result = AuditAdmissionRecoveryResult(
            store_health=AuditAdmissionStoreHealth(
                status=AuditAdmissionStorageHealthStatus.HEALTHY
            ),
            recovered_installations=(RecipeExecutionId("exec-1"),),
            recovered_attempts=(AuditAttemptId("attempt-1"),),
        )
        assert result.recovered_installations == (RecipeExecutionId("exec-1"),)


class TestAuditReservationRequest:
    def _kwargs(self, **overrides: object) -> dict[str, object]:
        base: dict[str, object] = {
            "recipe_execution_id": RecipeExecutionId("exec-1"),
            "installation_version": InstallationVersion("install-1"),
            "step_name": "audit-impl",
            "invocation_template_digest": _digest("t"),
            "slot_intent_digest": _digest("i"),
            "runtime_binding_digest": _digest("rb"),
            "audited_plan_refs": (_ref(),),
            "cycle_id": "cycle-1",
            "scope_id": "scope-1",
            "part_id": "part-1",
            "allowed_root": Path("/tmp/audit-admission"),
        }
        base.update(overrides)
        return base

    def test_valid_request_constructs(self) -> None:
        request = AuditReservationRequest(**self._kwargs())  # type: ignore[arg-type]
        assert request.parent_authority_digest is None
        assert request.retry_after_audit_attempt_id is None

    def test_rejects_empty_audited_plan_refs(self) -> None:
        with pytest.raises(ValueError, match="audited_plan_refs must be non-empty"):
            AuditReservationRequest(**self._kwargs(audited_plan_refs=()))  # type: ignore[arg-type]

    def test_rejects_malformed_digest(self) -> None:
        with pytest.raises(ValueError, match="algorithm-qualified"):
            AuditReservationRequest(**self._kwargs(runtime_binding_digest="not-a-digest"))  # type: ignore[arg-type]

    def test_rejects_relative_allowed_root(self) -> None:
        with pytest.raises(ValueError, match="absolute non-traversing"):
            AuditReservationRequest(**self._kwargs(allowed_root=Path("relative")))  # type: ignore[arg-type]

    def test_accepts_explicit_parent_authority_digest(self) -> None:
        request = AuditReservationRequest(
            **self._kwargs(parent_authority_digest=_digest("p"))  # type: ignore[arg-type]
        )
        assert request.parent_authority_digest == _digest("p")


class TestAuditReservationOutcome:
    def test_dispatch_requires_reservation_and_handle(self) -> None:
        with pytest.raises(ValueError, match="requires a reservation and handle"):
            AuditReservationOutcome(
                decision=ReservationDecision.DISPATCH_NEW,
                slot_key=_slot_key(),
                attempt_id=AuditAttemptId("attempt-1"),
            )

    def test_dispatch_accepts_reservation_and_handle(self) -> None:
        outcome = AuditReservationOutcome(
            decision=ReservationDecision.DISPATCH_NEW,
            slot_key=_slot_key(),
            attempt_id=AuditAttemptId("attempt-1"),
            reservation=_reservation(),
            reservation_handle="handle",
        )
        assert outcome.reservation_handle == "handle"

    def test_resume_cannot_reissue_handle(self) -> None:
        with pytest.raises(ValueError, match="never reissues a handle"):
            AuditReservationOutcome(
                decision=ReservationDecision.RESUME_PREPARED,
                slot_key=_slot_key(),
                attempt_id=AuditAttemptId("attempt-1"),
                reservation=_reservation(),
                reservation_handle="handle",
            )

    def test_exact_replay_requires_replay_outcome(self) -> None:
        with pytest.raises(ValueError, match="EXACT_REPLAY requires replay_outcome"):
            AuditReservationOutcome(
                decision=ReservationDecision.EXACT_REPLAY,
                slot_key=_slot_key(),
                attempt_id=AuditAttemptId("attempt-1"),
            )

    def test_exact_replay_cannot_dispatch(self) -> None:
        outcome = AuditOutcome(
            status=AuditOutcomeStatus.PUBLISHED,
            attempt_id=AuditAttemptId("attempt-1"),
            verdict=AuditVerdict.GO,
            path=Path("/tmp/authority.json"),
            error=None,
        )
        with pytest.raises(ValueError, match="never dispatches a child"):
            AuditReservationOutcome(
                decision=ReservationDecision.EXACT_REPLAY,
                slot_key=_slot_key(),
                attempt_id=AuditAttemptId("attempt-1"),
                replay_outcome=outcome,
                reservation=_reservation(),
            )

    def test_conflict_requires_detail(self) -> None:
        with pytest.raises(ValueError, match="CONFLICT requires conflict_detail"):
            AuditReservationOutcome(
                decision=ReservationDecision.CONFLICT,
                slot_key=_slot_key(),
                attempt_id=AuditAttemptId("attempt-1"),
            )

    def test_conflict_cannot_carry_dispatch_payload(self) -> None:
        with pytest.raises(ValueError, match="cannot carry reservation"):
            AuditReservationOutcome(
                decision=ReservationDecision.CONFLICT,
                slot_key=_slot_key(),
                attempt_id=AuditAttemptId("attempt-1"),
                conflict_detail="stale_head",
                reservation=_reservation(),
            )


class TestAuditPrepareRequest:
    def test_rejected_attempt_cannot_carry_effects(self) -> None:
        effect = AuditPreparedEffect(
            artifact_kind="authority",
            canonical_bytes=b"{}",
            content_digest="sha256:" + hashlib.sha256(b"{}").hexdigest(),
            path=Path("/tmp/authority.json"),
            delivery_status=AuditPreparedEffectDeliveryStatus.PENDING,
            canonicalization_profile="v1",
            semantic_fingerprint=_digest("f"),
        )
        with pytest.raises(ValueError, match="cannot carry prepared effects"):
            AuditPrepareRequest(
                attempt_id=AuditAttemptId("attempt-1"),
                installation_version=InstallationVersion("install-1"),
                semantic_digest=_digest("s"),
                accepted=False,
                effects=(effect,),
            )

    def test_accepted_request_constructs(self) -> None:
        request = AuditPrepareRequest(
            attempt_id=AuditAttemptId("attempt-1"),
            installation_version=InstallationVersion("install-1"),
            semantic_digest=_digest("s"),
            accepted=True,
        )
        assert request.effects == ()


class TestAuditPrepareOutcome:
    def test_accepted_cannot_carry_conflict_detail(self) -> None:
        with pytest.raises(ValueError, match="cannot carry conflict_detail"):
            AuditPrepareOutcome(
                accepted=True,
                attempt_id=AuditAttemptId("attempt-1"),
                conflict_detail="x",
            )


class TestAuditFinalCommitRequest:
    def test_requires_at_least_one_preflight_step_name(self) -> None:
        with pytest.raises(ValueError, match="must be non-empty"):
            AuditFinalCommitRequest(
                attempt_id=AuditAttemptId("attempt-1"),
                installation_version=InstallationVersion("install-1"),
                expected_head_digest=None,
                new_head=_head(),
                preflight_step_names=(),
            )

    def test_valid_request_constructs(self) -> None:
        request = AuditFinalCommitRequest(
            attempt_id=AuditAttemptId("attempt-1"),
            installation_version=InstallationVersion("install-1"),
            expected_head_digest=None,
            new_head=_head(),
            preflight_step_names=("make-plan",),
        )
        assert request.preflight_step_names == ("make-plan",)


class TestAuditFinalCommitOutcome:
    def test_rejected_requires_conflict_detail(self) -> None:
        with pytest.raises(ValueError, match="requires conflict_detail"):
            AuditFinalCommitOutcome(committed=False, attempt_id=AuditAttemptId("attempt-1"))

    def test_committed_cannot_carry_conflict_detail(self) -> None:
        with pytest.raises(ValueError, match="cannot carry conflict_detail"):
            AuditFinalCommitOutcome(
                committed=True,
                attempt_id=AuditAttemptId("attempt-1"),
                conflict_detail="x",
            )


class TestAuditPreflightProjection:
    def test_rejects_blank_field(self) -> None:
        with pytest.raises(ValueError, match="non-empty string"):
            AuditPreflightProjection(plan_set_id="", scope_id="scope-1", part_id="part-1")


class TestAuditDispositionCommitRequest:
    def _kwargs(self, **overrides: object) -> dict[str, object]:
        base: dict[str, object] = {
            "recipe_execution_id": RecipeExecutionId("exec-1"),
            "installation_version": InstallationVersion("install-1"),
            "cycle_id": "cycle-1",
            "scope_id": "scope-1",
            "part_id": "part-1",
            "authority_digest": _digest("a"),
            "plan_digest": _digest("p"),
            "report_digest": _digest("r"),
            "report_path": Path("/tmp/report.json"),
            "association_digest": _digest("s"),
            "association_path": Path("/tmp/assoc.json"),
            "generated_at": "2026-01-01T00:00:00Z",
        }
        base.update(overrides)
        return base

    def test_valid_request_constructs(self) -> None:
        request = AuditDispositionCommitRequest(**self._kwargs())  # type: ignore[arg-type]
        assert request.generated_at == "2026-01-01T00:00:00Z"

    def test_rejects_relative_report_path(self) -> None:
        with pytest.raises(ValueError, match="absolute non-traversing"):
            AuditDispositionCommitRequest(**self._kwargs(report_path=Path("relative.json")))  # type: ignore[arg-type]


class TestAuditDispositionCommitOutcome:
    def test_rejected_requires_conflict_detail(self) -> None:
        with pytest.raises(ValueError, match="requires conflict_detail"):
            AuditDispositionCommitOutcome(committed=False, generated_at="2026-01-01T00:00:00Z")

    def test_committed_cannot_carry_conflict_detail(self) -> None:
        with pytest.raises(ValueError, match="cannot carry conflict_detail"):
            AuditDispositionCommitOutcome(
                committed=True,
                generated_at="2026-01-01T00:00:00Z",
                conflict_detail="x",
            )
