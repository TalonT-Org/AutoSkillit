"""Crash-safe audit-admission ledger tests."""

from __future__ import annotations

import hashlib
import os
import sqlite3
import threading
from pathlib import Path

import pytest

from autoskillit.core import (
    ArtifactRef,
    AuditAdmissionStorageError,
    AuditAdmissionStorageFailureReason,
    AuditAdmissionStorageHealthStatus,
    AuditAdmissionStoreAuthority,
    AuditAttemptId,
    AuditCycleHead,
    AuditDispositionCommitRequest,
    AuditFinalCommitRequest,
    AuditOutcome,
    AuditOutcomeStatus,
    AuditPrepareRequest,
    AuditReservationOutcome,
    AuditReservationRequest,
    AuditVerdict,
    InstallationVersion,
    KillReason,
    RecipeExecutionId,
    ReservationDecision,
)
from autoskillit.pipeline import audit_admission_ledger as audit_ledger_module
from autoskillit.pipeline.audit_admission_ledger import DefaultAuditAdmissionLedger

pytestmark = [pytest.mark.layer("pipeline"), pytest.mark.medium]

_REQUIRED_FINALIZATION_EFFECTS = (
    "audit_success_recorded",
    "run_skill_state_cleared",
)


def _digest(tag: str) -> str:
    return "sha256:" + hashlib.sha256(tag.encode()).hexdigest()


def _authority(tmp_path: Path) -> AuditAdmissionStoreAuthority:
    return AuditAdmissionStoreAuthority(
        database_path=tmp_path / "audit-admission" / "ledger.sqlite3",
        expected_owner_id=os.getuid(),
    )


def _ref(root: Path, tag: str = "plan") -> ArtifactRef:
    return ArtifactRef(
        locator=str(root / f"{tag}.md"),
        media_type="text/markdown",
        schema_version=1,
        byte_size=10,
        content_digest=_digest(f"content-{tag}"),
    )


def _reservation_request(
    root: Path,
    execution_id: RecipeExecutionId,
    version: object,
    *,
    step_name: str = "audit-impl",
    template: str = "t",
    intent: str = "i",
    cycle_id: str = "cycle-1",
    scope_id: str = "scope-1",
    part_id: str = "part-1",
    parent_authority_digest: str | None = None,
    retry_after_audit_attempt_id: AuditAttemptId | None = None,
) -> AuditReservationRequest:
    return AuditReservationRequest(
        recipe_execution_id=execution_id,
        installation_version=version,  # type: ignore[arg-type]
        step_name=step_name,
        invocation_template_digest=_digest(f"template-{template}"),
        slot_intent_digest=_digest(f"intent-{intent}"),
        runtime_binding_digest=_digest(f"binding-{template}-{intent}"),
        audited_plan_refs=(_ref(root),),
        cycle_id=cycle_id,
        scope_id=scope_id,
        part_id=part_id,
        allowed_root=root,
        parent_authority_digest=parent_authority_digest,
        retry_after_audit_attempt_id=retry_after_audit_attempt_id,
    )


def _plan_set_id(outcome: AuditReservationOutcome) -> str:
    assert outcome.reservation is not None
    return outcome.reservation.plan_set_id


def _head(
    root: Path, execution_id: RecipeExecutionId, plan_set_id: str, **overrides: object
) -> AuditCycleHead:
    ref = _ref(root)
    base: dict[str, object] = {
        "execution_generation": execution_id.value,
        "cycle_id": "cycle-1",
        "plan_set_id": plan_set_id,
        "scope_id": "scope-1",
        "part_id": "part-1",
        "current_authority_digest": _digest("head-1"),
        "audit_round": 1,
        "audited_plan_refs": (ref,),
        "inventory_ref": ref,
        "verdict": AuditVerdict.GO,
    }
    base.update(overrides)
    return AuditCycleHead(**base)  # type: ignore[arg-type]


def _published_attempt(
    ledger: DefaultAuditAdmissionLedger,
    root: Path,
) -> tuple[AuditAttemptId, InstallationVersion]:
    execution_id = RecipeExecutionId("exec-1")
    version = ledger.create_or_get_installation(
        recipe_execution_id=execution_id,
        snapshot_digest=_digest("s"),
    )
    reserved = ledger.reserve(_reservation_request(root, execution_id, version))
    ledger.prepare(
        AuditPrepareRequest(
            attempt_id=reserved.attempt_id,
            installation_version=version,
            semantic_digest=_digest("semantic"),
            accepted=True,
        )
    )
    committed = ledger.commit_authority(
        AuditFinalCommitRequest(
            attempt_id=reserved.attempt_id,
            installation_version=version,
            expected_head_digest=None,
            new_head=_head(root, execution_id, _plan_set_id(reserved)),
            preflight_step_names=("make-plan",),
        )
    )
    assert committed.committed
    return reserved.attempt_id, version


def _acknowledge_required_finalization_effects(
    ledger: DefaultAuditAdmissionLedger,
    attempt_id: AuditAttemptId,
    *,
    effect_names: tuple[str, ...] = _REQUIRED_FINALIZATION_EFFECTS,
) -> None:
    for effect_name in effect_names:
        ledger.acknowledge_finalization_effect(
            attempt_id,
            effect_name,
            {"completed": True},
        )


class TestInstallations:
    def test_create_or_get_is_idempotent(self, tmp_path: Path) -> None:
        ledger = DefaultAuditAdmissionLedger(_authority(tmp_path))
        execution_id = RecipeExecutionId("exec-1")
        version1 = ledger.create_or_get_installation(
            recipe_execution_id=execution_id, snapshot_digest=_digest("s")
        )
        version2 = ledger.create_or_get_installation(
            recipe_execution_id=execution_id, snapshot_digest=_digest("s")
        )
        assert version1 == version2

    def test_retire_then_recreate_issues_a_new_version(self, tmp_path: Path) -> None:
        ledger = DefaultAuditAdmissionLedger(_authority(tmp_path))
        execution_id = RecipeExecutionId("exec-1")
        version1 = ledger.create_or_get_installation(
            recipe_execution_id=execution_id, snapshot_digest=_digest("s")
        )
        ledger.retire_installation(recipe_execution_id=execution_id, installation_version=version1)
        version2 = ledger.create_or_get_installation(
            recipe_execution_id=execution_id, snapshot_digest=_digest("s")
        )
        assert version1 != version2

    def test_active_installation_rejects_snapshot_mismatch(self, tmp_path: Path) -> None:
        ledger = DefaultAuditAdmissionLedger(_authority(tmp_path))
        execution_id = RecipeExecutionId("exec-1")
        version = ledger.create_or_get_installation(
            recipe_execution_id=execution_id,
            snapshot_digest=_digest("snapshot-a"),
        )

        with pytest.raises(AuditAdmissionStorageError) as captured:
            ledger.create_or_get_installation(
                recipe_execution_id=execution_id,
                snapshot_digest=_digest("snapshot-b"),
            )

        assert captured.value.reason is AuditAdmissionStorageFailureReason.REPLAY_MISMATCH
        assert (
            ledger.create_or_get_installation(
                recipe_execution_id=execution_id,
                snapshot_digest=_digest("snapshot-a"),
            )
            == version
        )

    def test_retire_recreate_retains_both_installation_occurrences(
        self,
        tmp_path: Path,
    ) -> None:
        authority = _authority(tmp_path)
        ledger = DefaultAuditAdmissionLedger(authority)
        execution_id = RecipeExecutionId("exec-1")
        version1 = ledger.create_or_get_installation(
            recipe_execution_id=execution_id,
            snapshot_digest=_digest("snapshot-a"),
        )
        ledger.retire_installation(
            recipe_execution_id=execution_id,
            installation_version=version1,
        )
        version2 = ledger.create_or_get_installation(
            recipe_execution_id=execution_id,
            snapshot_digest=_digest("snapshot-b"),
        )

        restarted = DefaultAuditAdmissionLedger(authority)
        assert (
            restarted.recover_all().store_health.status
            is AuditAdmissionStorageHealthStatus.HEALTHY
        )
        with sqlite3.connect(authority.database_path) as connection:
            rows = connection.execute(
                "SELECT installation_version, snapshot_digest, retired_at "
                "FROM installation_occurrences WHERE recipe_execution_id = ?",
                (execution_id.value,),
            ).fetchall()

        assert {(row[0], row[1]) for row in rows} == {
            (version1.value, _digest("snapshot-a")),
            (version2.value, _digest("snapshot-b")),
        }
        retired_by_version = {row[0]: row[2] for row in rows}
        assert retired_by_version[version1.value] is not None
        assert retired_by_version[version2.value] is None


class TestReservation:
    def test_dispatch_new_then_redispatch_open_rotates_handle(self, tmp_path: Path) -> None:
        ledger = DefaultAuditAdmissionLedger(_authority(tmp_path))
        execution_id = RecipeExecutionId("exec-1")
        version = ledger.create_or_get_installation(
            recipe_execution_id=execution_id, snapshot_digest=_digest("s")
        )
        request = _reservation_request(tmp_path, execution_id, version)

        first = ledger.reserve(request)
        assert first.decision is ReservationDecision.DISPATCH_NEW
        assert first.reservation is not None
        assert first.reservation_handle

        second = ledger.reserve(request)
        assert second.decision is ReservationDecision.REDISPATCH_OPEN
        assert second.attempt_id == first.attempt_id
        assert second.reservation_handle != first.reservation_handle

        # The rotated-away handle no longer resolves; the fresh one does.
        assert ledger.resolve_reservation_handle(first.reservation_handle) is None  # type: ignore[arg-type]
        resolved = ledger.resolve_reservation_handle(second.reservation_handle)  # type: ignore[arg-type]
        assert resolved is not None
        assert first.reservation is not None
        assert resolved.slot_id == first.reservation.slot_id

    def test_different_bound_inputs_produce_a_distinct_slot(self, tmp_path: Path) -> None:
        ledger = DefaultAuditAdmissionLedger(_authority(tmp_path))
        execution_id = RecipeExecutionId("exec-1")
        version = ledger.create_or_get_installation(
            recipe_execution_id=execution_id, snapshot_digest=_digest("s")
        )
        a = ledger.reserve(_reservation_request(tmp_path, execution_id, version, intent="a"))
        b = ledger.reserve(_reservation_request(tmp_path, execution_id, version, intent="b"))
        assert a.slot_key != b.slot_key
        assert a.attempt_id != b.attempt_id

    def test_handle_digest_collision_fails_closed(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        authority = _authority(tmp_path)
        ledger = DefaultAuditAdmissionLedger(authority)
        execution_id = RecipeExecutionId("exec-1")
        version = ledger.create_or_get_installation(
            recipe_execution_id=execution_id,
            snapshot_digest=_digest("s"),
        )
        monkeypatch.setattr(
            audit_ledger_module,
            "compute_bytes_hash",
            lambda _data: _digest("forced-handle-collision"),
        )

        first = ledger.reserve(_reservation_request(tmp_path, execution_id, version, intent="a"))
        with pytest.raises(
            sqlite3.IntegrityError,
            match=r"UNIQUE constraint failed: attempts\.handle_digest",
        ):
            ledger.reserve(_reservation_request(tmp_path, execution_id, version, intent="b"))

        assert first.reservation_handle is not None
        assert ledger.resolve_reservation_handle(first.reservation_handle) is not None
        with sqlite3.connect(authority.database_path) as connection:
            attempt_count = connection.execute("SELECT COUNT(*) FROM attempts").fetchone()
        assert attempt_count == (1,)

    def test_reserve_without_installation_raises(self, tmp_path: Path) -> None:
        ledger = DefaultAuditAdmissionLedger(_authority(tmp_path))
        execution_id = RecipeExecutionId("exec-1")

        request = _reservation_request(tmp_path, execution_id, InstallationVersion("unknown"))
        with pytest.raises(ValueError, match="create_or_get_installation"):
            ledger.reserve(request)

    def test_retired_installation_conflicts(self, tmp_path: Path) -> None:
        ledger = DefaultAuditAdmissionLedger(_authority(tmp_path))
        execution_id = RecipeExecutionId("exec-1")
        version = ledger.create_or_get_installation(
            recipe_execution_id=execution_id, snapshot_digest=_digest("s")
        )
        ledger.retire_installation(recipe_execution_id=execution_id, installation_version=version)
        outcome = ledger.reserve(_reservation_request(tmp_path, execution_id, version))
        assert outcome.decision is ReservationDecision.CONFLICT
        assert outcome.conflict_detail == "installation_retired"


class TestMaterializationLifecycle:
    def test_prepare_reject_then_replay_conflicts_without_a_token(self, tmp_path: Path) -> None:
        ledger = DefaultAuditAdmissionLedger(_authority(tmp_path))
        execution_id = RecipeExecutionId("exec-1")
        version = ledger.create_or_get_installation(
            recipe_execution_id=execution_id, snapshot_digest=_digest("s")
        )
        request = _reservation_request(tmp_path, execution_id, version)
        reserved = ledger.reserve(request)

        rejected = ledger.prepare(
            AuditPrepareRequest(
                attempt_id=reserved.attempt_id,
                installation_version=version,  # type: ignore[arg-type]
                semantic_digest=_digest("semantic"),
                accepted=False,
            )
        )
        assert not rejected.accepted

        replay = ledger.reserve(request)
        assert replay.decision is ReservationDecision.CONFLICT
        assert replay.conflict_detail == "correction_token_required"

    def test_correction_token_allocates_exactly_one_next_attempt(self, tmp_path: Path) -> None:
        ledger = DefaultAuditAdmissionLedger(_authority(tmp_path))
        execution_id = RecipeExecutionId("exec-1")
        version = ledger.create_or_get_installation(
            recipe_execution_id=execution_id, snapshot_digest=_digest("s")
        )
        request = _reservation_request(tmp_path, execution_id, version)
        reserved = ledger.reserve(request)
        ledger.prepare(
            AuditPrepareRequest(
                attempt_id=reserved.attempt_id,
                installation_version=version,  # type: ignore[arg-type]
                semantic_digest=_digest("semantic"),
                accepted=False,
            )
        )

        corrected = ledger.reserve(
            _reservation_request(
                tmp_path,
                execution_id,
                version,
                retry_after_audit_attempt_id=reserved.attempt_id,
            )
        )
        assert corrected.decision is ReservationDecision.DISPATCH_NEW
        assert corrected.attempt_id != reserved.attempt_id

        # A stale/wrong token no longer names the (now-corrected) attempt's slot state.
        stale = ledger.reserve(
            _reservation_request(
                tmp_path,
                execution_id,
                version,
                retry_after_audit_attempt_id=AuditAttemptId("wrong-attempt"),
            )
        )
        assert stale.decision is ReservationDecision.CONFLICT

    def test_prepare_accept_then_resume_prepared_on_redelivery(self, tmp_path: Path) -> None:
        ledger = DefaultAuditAdmissionLedger(_authority(tmp_path))
        execution_id = RecipeExecutionId("exec-1")
        version = ledger.create_or_get_installation(
            recipe_execution_id=execution_id, snapshot_digest=_digest("s")
        )
        request = _reservation_request(tmp_path, execution_id, version)
        reserved = ledger.reserve(request)
        prepared = ledger.prepare(
            AuditPrepareRequest(
                attempt_id=reserved.attempt_id,
                installation_version=version,  # type: ignore[arg-type]
                semantic_digest=_digest("semantic"),
                accepted=True,
            )
        )
        assert prepared.accepted

        resumed = ledger.reserve(request)
        assert resumed.decision is ReservationDecision.RESUME_PREPARED
        assert resumed.attempt_id == reserved.attempt_id
        assert resumed.reservation_handle is None

    def test_commit_authority_then_published_pending_finalization_on_redelivery(
        self, tmp_path: Path
    ) -> None:
        ledger = DefaultAuditAdmissionLedger(_authority(tmp_path))
        execution_id = RecipeExecutionId("exec-1")
        version = ledger.create_or_get_installation(
            recipe_execution_id=execution_id, snapshot_digest=_digest("s")
        )
        request = _reservation_request(tmp_path, execution_id, version)
        reserved = ledger.reserve(request)
        ledger.prepare(
            AuditPrepareRequest(
                attempt_id=reserved.attempt_id,
                installation_version=version,  # type: ignore[arg-type]
                semantic_digest=_digest("semantic"),
                accepted=True,
            )
        )
        head = _head(tmp_path, execution_id, _plan_set_id(reserved))
        committed = ledger.commit_authority(
            AuditFinalCommitRequest(
                attempt_id=reserved.attempt_id,
                installation_version=version,  # type: ignore[arg-type]
                expected_head_digest=None,
                new_head=head,
                preflight_step_names=("make-plan",),
            )
        )
        assert committed.committed

        pending = ledger.reserve(request)
        assert pending.decision is ReservationDecision.PUBLISHED_PENDING_FINALIZATION
        assert pending.reservation_handle is None

    def test_finalize_response_then_exact_replay(self, tmp_path: Path) -> None:
        ledger = DefaultAuditAdmissionLedger(_authority(tmp_path))
        execution_id = RecipeExecutionId("exec-1")
        version = ledger.create_or_get_installation(
            recipe_execution_id=execution_id, snapshot_digest=_digest("s")
        )
        request = _reservation_request(tmp_path, execution_id, version)
        reserved = ledger.reserve(request)
        ledger.prepare(
            AuditPrepareRequest(
                attempt_id=reserved.attempt_id,
                installation_version=version,  # type: ignore[arg-type]
                semantic_digest=_digest("semantic"),
                accepted=True,
            )
        )
        head = _head(tmp_path, execution_id, _plan_set_id(reserved))
        ledger.commit_authority(
            AuditFinalCommitRequest(
                attempt_id=reserved.attempt_id,
                installation_version=version,  # type: ignore[arg-type]
                expected_head_digest=None,
                new_head=head,
                preflight_step_names=("make-plan",),
            )
        )
        outcome = AuditOutcome(
            status=AuditOutcomeStatus.PUBLISHED,
            attempt_id=reserved.attempt_id,
            verdict=AuditVerdict.GO,
            path=tmp_path / "authority.json",
            error=None,
            kill_reason=KillReason.INFRA_KILL,
            replay_response_json=(
                '{"success":true,"kill_reason":"infra_kill","audit_status":"EXACT_REPLAY"}'
            ),
        )
        _acknowledge_required_finalization_effects(ledger, reserved.attempt_id)
        ledger.finalize_response(
            reserved.attempt_id,
            outcome,
            required_effect_names=_REQUIRED_FINALIZATION_EFFECTS,
        )
        # Idempotent re-finalize with the identical outcome is a no-op, not an error.
        ledger.finalize_response(
            reserved.attempt_id,
            outcome,
            required_effect_names=_REQUIRED_FINALIZATION_EFFECTS,
        )
        with pytest.raises(
            AuditAdmissionStorageError,
            match="finalize-response-commit-mismatch",
        ):
            ledger.finalize_response(
                reserved.attempt_id,
                outcome,
                required_effect_names=(
                    *_REQUIRED_FINALIZATION_EFFECTS,
                    "pipeline_step_completed",
                ),
            )

        replay = ledger.reserve(request)
        assert replay.decision is ReservationDecision.EXACT_REPLAY
        assert replay.replay_outcome == outcome
        assert replay.replay_outcome.kill_reason is KillReason.INFRA_KILL
        assert '"audit_status":"EXACT_REPLAY"' in (
            replay.replay_outcome.replay_response_json or ""
        )

        projection = ledger.preflight_projection(
            recipe_execution_id=execution_id,
            installation_version=version,  # type: ignore[arg-type]
            step_name="make-plan",
        )
        assert projection is not None
        assert projection.scope_id == "scope-1"

        current = ledger.current_head(
            recipe_execution_id=execution_id,
            cycle_id="cycle-1",
            scope_id="scope-1",
            part_id="part-1",
        )
        assert current is not None
        assert current.current_authority_digest == head.current_authority_digest

    def test_finalize_response_rejects_a_conflicting_outcome_for_the_same_attempt(
        self, tmp_path: Path
    ) -> None:
        ledger = DefaultAuditAdmissionLedger(_authority(tmp_path))
        execution_id = RecipeExecutionId("exec-1")
        version = ledger.create_or_get_installation(
            recipe_execution_id=execution_id, snapshot_digest=_digest("s")
        )
        request = _reservation_request(tmp_path, execution_id, version)
        reserved = ledger.reserve(request)
        ledger.prepare(
            AuditPrepareRequest(
                attempt_id=reserved.attempt_id,
                installation_version=version,  # type: ignore[arg-type]
                semantic_digest=_digest("semantic"),
                accepted=True,
            )
        )
        head = _head(tmp_path, execution_id, _plan_set_id(reserved))
        ledger.commit_authority(
            AuditFinalCommitRequest(
                attempt_id=reserved.attempt_id,
                installation_version=version,  # type: ignore[arg-type]
                expected_head_digest=None,
                new_head=head,
                preflight_step_names=("make-plan",),
            )
        )
        first_outcome = AuditOutcome(
            status=AuditOutcomeStatus.PUBLISHED,
            attempt_id=reserved.attempt_id,
            verdict=AuditVerdict.GO,
            path=tmp_path / "authority.json",
            error=None,
            replay_response_json='{"audit_status":"EXACT_REPLAY","path":"authority.json"}',
        )
        _acknowledge_required_finalization_effects(ledger, reserved.attempt_id)
        ledger.finalize_response(
            reserved.attempt_id,
            first_outcome,
            required_effect_names=_REQUIRED_FINALIZATION_EFFECTS,
        )
        conflicting_outcome = AuditOutcome(
            status=AuditOutcomeStatus.PUBLISHED,
            attempt_id=reserved.attempt_id,
            verdict=AuditVerdict.GO,
            path=tmp_path / "different-authority.json",
            error=None,
            replay_response_json=(
                '{"audit_status":"EXACT_REPLAY","path":"different-authority.json"}'
            ),
        )
        with pytest.raises(Exception, match="finalize-response-commit-mismatch"):
            ledger.finalize_response(
                reserved.attempt_id,
                conflicting_outcome,
                required_effect_names=_REQUIRED_FINALIZATION_EFFECTS,
            )


class TestFinalizationEffects:
    def test_open_attempt_cannot_read_or_acknowledge_finalization_effects(
        self,
        tmp_path: Path,
    ) -> None:
        ledger = DefaultAuditAdmissionLedger(_authority(tmp_path))
        execution_id = RecipeExecutionId("exec-1")
        version = ledger.create_or_get_installation(
            recipe_execution_id=execution_id,
            snapshot_digest=_digest("s"),
        )
        reserved = ledger.reserve(_reservation_request(tmp_path, execution_id, version))

        with pytest.raises(ValueError, match="not eligible for finalization"):
            ledger.finalization_effect_result(reserved.attempt_id, "audit_success")
        with pytest.raises(ValueError, match="not eligible for finalization"):
            ledger.acknowledge_finalization_effect(
                reserved.attempt_id,
                "audit_success",
                {"recorded": True},
            )

    def test_acknowledgements_are_durable_idempotent_and_immutable(
        self,
        tmp_path: Path,
    ) -> None:
        authority = _authority(tmp_path)
        ledger = DefaultAuditAdmissionLedger(authority)
        attempt_id, _ = _published_attempt(ledger, tmp_path)
        result = {"recorded": True, "sequence": 1}

        assert ledger.finalization_effect_result(attempt_id, "audit_success") is None
        ledger.acknowledge_finalization_effect(attempt_id, "audit_success", result)
        ledger.acknowledge_finalization_effect(attempt_id, "audit_success", result)
        loaded = ledger.finalization_effect_result(attempt_id, "audit_success")
        assert loaded == result
        assert loaded is not result

        restarted = DefaultAuditAdmissionLedger(authority)
        assert restarted.finalization_effect_result(attempt_id, "audit_success") == result
        with pytest.raises(AuditAdmissionStorageError) as captured:
            restarted.acknowledge_finalization_effect(
                attempt_id,
                "audit_success",
                {"recorded": False, "sequence": 1},
            )
        assert captured.value.reason is AuditAdmissionStorageFailureReason.INTEGRITY

    def test_finalize_requires_replay_projection_and_every_declared_effect(
        self,
        tmp_path: Path,
    ) -> None:
        ledger = DefaultAuditAdmissionLedger(_authority(tmp_path))
        attempt_id, _ = _published_attempt(ledger, tmp_path)
        outcome_without_replay = AuditOutcome(
            status=AuditOutcomeStatus.PUBLISHED,
            attempt_id=attempt_id,
            verdict=AuditVerdict.GO,
            path=tmp_path / "authority.json",
            error=None,
        )
        with pytest.raises(ValueError, match="requires replay_response_json"):
            ledger.finalize_response(
                attempt_id,
                outcome_without_replay,
                required_effect_names=_REQUIRED_FINALIZATION_EFFECTS,
            )

        ledger.acknowledge_finalization_effect(
            attempt_id,
            _REQUIRED_FINALIZATION_EFFECTS[0],
            {"completed": True},
        )
        outcome = AuditOutcome(
            status=AuditOutcomeStatus.PUBLISHED,
            attempt_id=attempt_id,
            verdict=AuditVerdict.GO,
            path=tmp_path / "authority.json",
            error=None,
            replay_response_json='{"audit_status":"EXACT_REPLAY"}',
        )
        with pytest.raises(
            AuditAdmissionStorageError,
            match="finalize-response-required-effects-missing",
        ):
            ledger.finalize_response(
                attempt_id,
                outcome,
                required_effect_names=_REQUIRED_FINALIZATION_EFFECTS,
            )

        assert (
            ledger.reserve(
                _reservation_request(
                    tmp_path,
                    RecipeExecutionId("exec-1"),
                    ledger.create_or_get_installation(
                        recipe_execution_id=RecipeExecutionId("exec-1"),
                        snapshot_digest=_digest("s"),
                    ),
                )
            ).decision
            is ReservationDecision.PUBLISHED_PENDING_FINALIZATION
        )

    def test_response_commit_fault_rolls_back_projection_and_lifecycle(
        self,
        tmp_path: Path,
    ) -> None:
        authority = _authority(tmp_path)
        ledger = DefaultAuditAdmissionLedger(authority)
        attempt_id, version = _published_attempt(ledger, tmp_path)
        _acknowledge_required_finalization_effects(ledger, attempt_id)
        outcome = AuditOutcome(
            status=AuditOutcomeStatus.PUBLISHED,
            attempt_id=attempt_id,
            verdict=AuditVerdict.GO,
            path=tmp_path / "authority.json",
            error=None,
            replay_response_json='{"audit_status":"EXACT_REPLAY"}',
        )
        with sqlite3.connect(authority.database_path) as connection:
            connection.execute(
                "CREATE TRIGGER inject_response_commit_fault "
                "BEFORE UPDATE OF lifecycle ON attempts "
                "WHEN NEW.lifecycle = 'RESPONSE_COMMITTED' "
                "BEGIN SELECT RAISE(ABORT, 'injected-response-commit-fault'); END"
            )

        with pytest.raises(sqlite3.IntegrityError, match="injected-response-commit-fault"):
            ledger.finalize_response(
                attempt_id,
                outcome,
                required_effect_names=_REQUIRED_FINALIZATION_EFFECTS,
            )

        with sqlite3.connect(authority.database_path) as connection:
            response_commit_count = connection.execute(
                "SELECT COUNT(*) FROM response_commits WHERE attempt_id = ?",
                (attempt_id.value,),
            ).fetchone()
            connection.execute("DROP TRIGGER inject_response_commit_fault")
        assert response_commit_count == (0,)

        restarted = DefaultAuditAdmissionLedger(authority)
        replay = restarted.reserve(
            _reservation_request(
                tmp_path,
                RecipeExecutionId("exec-1"),
                version,
            )
        )
        assert replay.decision is ReservationDecision.PUBLISHED_PENDING_FINALIZATION

    def test_response_committed_attempt_rejects_late_effects_but_allows_reads(
        self,
        tmp_path: Path,
    ) -> None:
        ledger = DefaultAuditAdmissionLedger(_authority(tmp_path))
        attempt_id, _ = _published_attempt(ledger, tmp_path)
        _acknowledge_required_finalization_effects(ledger, attempt_id)
        ledger.finalize_response(
            attempt_id,
            AuditOutcome(
                status=AuditOutcomeStatus.PUBLISHED,
                attempt_id=attempt_id,
                verdict=AuditVerdict.GO,
                path=tmp_path / "authority.json",
                error=None,
                replay_response_json='{"audit_status":"EXACT_REPLAY"}',
            ),
            required_effect_names=_REQUIRED_FINALIZATION_EFFECTS,
        )

        assert ledger.finalization_effect_result(
            attempt_id, _REQUIRED_FINALIZATION_EFFECTS[0]
        ) == {"completed": True}
        with pytest.raises(ValueError, match="not eligible for finalization"):
            ledger.acknowledge_finalization_effect(
                attempt_id,
                "pipeline_step_completed",
                {"completed": True},
            )


class TestForkPrevention:
    def test_two_competing_initial_slots_only_one_commits(self, tmp_path: Path) -> None:
        ledger = DefaultAuditAdmissionLedger(_authority(tmp_path))
        execution_id = RecipeExecutionId("exec-1")
        version = ledger.create_or_get_installation(
            recipe_execution_id=execution_id, snapshot_digest=_digest("s")
        )
        a = ledger.reserve(_reservation_request(tmp_path, execution_id, version, intent="a"))
        b = ledger.reserve(_reservation_request(tmp_path, execution_id, version, intent="b"))
        assert a.slot_key != b.slot_key

        for outcome in (a, b):
            ledger.prepare(
                AuditPrepareRequest(
                    attempt_id=outcome.attempt_id,
                    installation_version=version,  # type: ignore[arg-type]
                    semantic_digest=_digest(f"semantic-{outcome.attempt_id.value}"),
                    accepted=True,
                )
            )

        head = _head(tmp_path, execution_id, _plan_set_id(a))
        commit_a = ledger.commit_authority(
            AuditFinalCommitRequest(
                attempt_id=a.attempt_id,
                installation_version=version,  # type: ignore[arg-type]
                expected_head_digest=None,
                new_head=head,
                preflight_step_names=("make-plan",),
            )
        )
        assert commit_a.committed

        commit_b = ledger.commit_authority(
            AuditFinalCommitRequest(
                attempt_id=b.attempt_id,
                installation_version=version,  # type: ignore[arg-type]
                expected_head_digest=None,
                new_head=head,
                preflight_step_names=("make-plan",),
            )
        )
        assert not commit_b.committed
        assert commit_b.conflict_detail == "stale_head"

    def test_terminal_go_head_cannot_be_advanced(self, tmp_path: Path) -> None:
        ledger = DefaultAuditAdmissionLedger(_authority(tmp_path))
        execution_id = RecipeExecutionId("exec-1")
        version = ledger.create_or_get_installation(
            recipe_execution_id=execution_id, snapshot_digest=_digest("s")
        )
        first = ledger.reserve(_reservation_request(tmp_path, execution_id, version))
        ledger.prepare(
            AuditPrepareRequest(
                attempt_id=first.attempt_id,
                installation_version=version,  # type: ignore[arg-type]
                semantic_digest=_digest("semantic"),
                accepted=True,
            )
        )
        head = _head(tmp_path, execution_id, _plan_set_id(first), verdict=AuditVerdict.GO)  # type: ignore[union-attr]
        ledger.commit_authority(
            AuditFinalCommitRequest(
                attempt_id=first.attempt_id,
                installation_version=version,  # type: ignore[arg-type]
                expected_head_digest=None,
                new_head=head,
                preflight_step_names=("make-plan",),
            )
        )

        successor = ledger.reserve(
            _reservation_request(
                tmp_path,
                execution_id,
                version,
                intent="successor",
                parent_authority_digest=head.current_authority_digest,
            )
        )
        assert successor.reservation is not None
        assert successor.reservation.audit_round.value == 2
        ledger.prepare(
            AuditPrepareRequest(
                attempt_id=successor.attempt_id,
                installation_version=version,  # type: ignore[arg-type]
                semantic_digest=_digest("semantic-2"),
                accepted=True,
            )
        )
        new_head = _head(
            tmp_path,
            execution_id,
            _plan_set_id(successor),
            audit_round=2,
            current_authority_digest=_digest("head-2"),
        )
        rejected = ledger.commit_authority(
            AuditFinalCommitRequest(
                attempt_id=successor.attempt_id,
                installation_version=version,  # type: ignore[arg-type]
                expected_head_digest=head.current_authority_digest,
                new_head=new_head,
                preflight_step_names=("make-plan",),
            )
        )
        assert not rejected.committed
        assert rejected.conflict_detail == "terminal_head"

    def test_concurrent_competing_initial_commits_have_exactly_one_winner(
        self, tmp_path: Path
    ) -> None:
        ledger = DefaultAuditAdmissionLedger(_authority(tmp_path))
        execution_id = RecipeExecutionId("exec-race")
        version = ledger.create_or_get_installation(
            recipe_execution_id=execution_id, snapshot_digest=_digest("s")
        )

        worker_count = 8
        results: list[bool] = [False] * worker_count
        barrier = threading.Barrier(worker_count)

        def worker(index: int) -> None:
            request = _reservation_request(
                tmp_path,
                execution_id,
                version,
                template=f"race-{index}",
                intent=f"race-{index}",
            )
            reserved = ledger.reserve(request)
            ledger.prepare(
                AuditPrepareRequest(
                    attempt_id=reserved.attempt_id,
                    installation_version=version,  # type: ignore[arg-type]
                    semantic_digest=_digest(f"race-semantic-{index}"),
                    accepted=True,
                )
            )
            head = _head(
                tmp_path,
                execution_id,
                _plan_set_id(reserved),
                current_authority_digest=_digest(f"race-head-{index}"),
            )
            barrier.wait()
            commit = ledger.commit_authority(
                AuditFinalCommitRequest(
                    attempt_id=reserved.attempt_id,
                    installation_version=version,  # type: ignore[arg-type]
                    expected_head_digest=None,
                    new_head=head,
                    preflight_step_names=("make-plan",),
                )
            )
            results[index] = commit.committed

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(worker_count)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        assert sum(1 for won in results if won) == 1


class TestDisposition:
    def _published_head(
        self,
        ledger: DefaultAuditAdmissionLedger,
        tmp_path: Path,
        execution_id: RecipeExecutionId,
        version: object,
    ) -> AuditCycleHead:
        request = _reservation_request(tmp_path, execution_id, version)
        reserved = ledger.reserve(request)
        ledger.prepare(
            AuditPrepareRequest(
                attempt_id=reserved.attempt_id,
                installation_version=version,  # type: ignore[arg-type]
                semantic_digest=_digest("semantic"),
                accepted=True,
            )
        )
        head = _head(tmp_path, execution_id, _plan_set_id(reserved), verdict=AuditVerdict.NO_GO)
        ledger.commit_authority(
            AuditFinalCommitRequest(
                attempt_id=reserved.attempt_id,
                installation_version=version,  # type: ignore[arg-type]
                expected_head_digest=None,
                new_head=head,
                preflight_step_names=("make-plan",),
            )
        )
        return head

    def test_commit_disposition_is_idempotent_and_resolvable(self, tmp_path: Path) -> None:
        ledger = DefaultAuditAdmissionLedger(_authority(tmp_path))
        execution_id = RecipeExecutionId("exec-1")
        version = ledger.create_or_get_installation(
            recipe_execution_id=execution_id, snapshot_digest=_digest("s")
        )
        head = self._published_head(ledger, tmp_path, execution_id, version)

        request = AuditDispositionCommitRequest(
            recipe_execution_id=execution_id,
            installation_version=version,  # type: ignore[arg-type]
            cycle_id="cycle-1",
            scope_id="scope-1",
            part_id="part-1",
            authority_digest=head.current_authority_digest,
            plan_digest=_digest("plan"),
            report_digest=_digest("report"),
            report_path=tmp_path / "report.json",
            association_digest=_digest("assoc"),
            association_path=tmp_path / "assoc.json",
            generated_at="2026-01-01T00:00:00Z",
        )
        first = ledger.commit_disposition(request)
        assert first.committed
        second = ledger.commit_disposition(request)
        assert second.committed
        assert second.generated_at == first.generated_at

        resolved = ledger.resolve_disposition(
            authority_digest=head.current_authority_digest, plan_digest=_digest("plan")
        )
        assert resolved == tmp_path / "report.json"

    def test_stale_authority_is_rejected(self, tmp_path: Path) -> None:
        ledger = DefaultAuditAdmissionLedger(_authority(tmp_path))
        execution_id = RecipeExecutionId("exec-1")
        version = ledger.create_or_get_installation(
            recipe_execution_id=execution_id, snapshot_digest=_digest("s")
        )
        self._published_head(ledger, tmp_path, execution_id, version)

        request = AuditDispositionCommitRequest(
            recipe_execution_id=execution_id,
            installation_version=version,  # type: ignore[arg-type]
            cycle_id="cycle-1",
            scope_id="scope-1",
            part_id="part-1",
            authority_digest=_digest("not-the-current-head"),
            plan_digest=_digest("plan"),
            report_digest=_digest("report"),
            report_path=tmp_path / "report.json",
            association_digest=_digest("assoc"),
            association_path=tmp_path / "assoc.json",
            generated_at="2026-01-01T00:00:00Z",
        )
        outcome = ledger.commit_disposition(request)
        assert not outcome.committed
        assert outcome.conflict_detail == "stale_authority"


class TestStoreSecurity:
    def test_new_database_is_private_owned_regular_and_single_linked(
        self,
        tmp_path: Path,
    ) -> None:
        authority = _authority(tmp_path)
        ledger = DefaultAuditAdmissionLedger(authority)
        ledger.create_or_get_installation(
            recipe_execution_id=RecipeExecutionId("exec-1"),
            snapshot_digest=_digest("s"),
        )

        database_stat = authority.database_path.stat()
        assert authority.database_path.is_file()
        assert database_stat.st_uid == authority.expected_owner_id
        assert database_stat.st_nlink == 1
        assert database_stat.st_mode & 0o022 == 0

    @pytest.mark.parametrize(
        "scenario",
        ["symlink", "non_regular", "hardlink", "world_writable", "foreign_owner"],
    )
    def test_insecure_database_target_fails_closed(
        self,
        tmp_path: Path,
        scenario: str,
    ) -> None:
        authority = _authority(tmp_path)
        path = authority.database_path
        path.parent.mkdir(mode=0o700)
        if scenario == "symlink":
            target = tmp_path / "symlink-target.sqlite3"
            target.write_bytes(b"")
            target.chmod(0o600)
            path.symlink_to(target)
        elif scenario == "non_regular":
            path.mkdir()
        elif scenario == "hardlink":
            target = tmp_path / "hardlink-target.sqlite3"
            target.write_bytes(b"")
            target.chmod(0o600)
            os.link(target, path)
        else:
            path.write_bytes(b"")
            path.chmod(0o602 if scenario == "world_writable" else 0o600)
            if scenario == "foreign_owner":
                authority = AuditAdmissionStoreAuthority(
                    database_path=path,
                    expected_owner_id=os.getuid() + 1,
                )

        ledger = DefaultAuditAdmissionLedger(authority)
        recovery = ledger.recover_all()

        assert recovery.store_health.status is AuditAdmissionStorageHealthStatus.FAIL_CLOSED
        assert (
            recovery.store_health.failure_reason
            is AuditAdmissionStorageFailureReason.SECURITY_IDENTITY
        )
        with pytest.raises(AuditAdmissionStorageError):
            ledger.create_or_get_installation(
                recipe_execution_id=RecipeExecutionId("exec-1"),
                snapshot_digest=_digest("s"),
            )

    def test_database_path_with_symlinked_ancestor_fails_closed(
        self,
        tmp_path: Path,
    ) -> None:
        real_root = tmp_path / "real"
        nested = real_root / "nested"
        nested.mkdir(parents=True, mode=0o700)
        linked_root = tmp_path / "linked"
        linked_root.symlink_to(real_root, target_is_directory=True)
        authority = AuditAdmissionStoreAuthority(
            database_path=linked_root / "nested" / "ledger.sqlite3",
            expected_owner_id=os.getuid(),
        )

        recovery = DefaultAuditAdmissionLedger(authority).recover_all()

        assert recovery.store_health.status is AuditAdmissionStorageHealthStatus.FAIL_CLOSED
        assert (
            recovery.store_health.failure_reason
            is AuditAdmissionStorageFailureReason.SECURITY_IDENTITY
        )

    def test_sqlite_error_during_recovery_fails_closed(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        ledger = DefaultAuditAdmissionLedger(_authority(tmp_path))

        def fail_connect() -> sqlite3.Connection:
            raise sqlite3.OperationalError("injected recovery fault")

        monkeypatch.setattr(ledger, "_connect", fail_connect)
        recovery = ledger.recover_all()

        assert recovery.store_health.status is AuditAdmissionStorageHealthStatus.FAIL_CLOSED
        assert recovery.store_health.failure_reason is AuditAdmissionStorageFailureReason.IO
        assert recovery.store_health.reason_code == (
            "audit-admission-recovery-failed:OperationalError"
        )


class TestRetention:
    def test_indefinite_policy_supports_far_late_replay_and_rejects_key_reuse(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        authority = _authority(tmp_path)
        monkeypatch.setattr(
            audit_ledger_module,
            "_now_iso",
            lambda: "1900-01-01T00:00:00+00:00",
        )
        ledger = DefaultAuditAdmissionLedger(authority)
        attempt_id, version = _published_attempt(ledger, tmp_path)
        request = _reservation_request(
            tmp_path,
            RecipeExecutionId("exec-1"),
            version,
        )
        _acknowledge_required_finalization_effects(ledger, attempt_id)
        outcome = AuditOutcome(
            status=AuditOutcomeStatus.PUBLISHED,
            attempt_id=attempt_id,
            verdict=AuditVerdict.GO,
            path=tmp_path / "authority.json",
            error=None,
            replay_response_json='{"audit_status":"EXACT_REPLAY"}',
        )
        ledger.finalize_response(
            attempt_id,
            outcome,
            required_effect_names=_REQUIRED_FINALIZATION_EFFECTS,
        )

        monkeypatch.setattr(
            audit_ledger_module,
            "_now_iso",
            lambda: "9999-12-31T23:59:59+00:00",
        )
        restarted = DefaultAuditAdmissionLedger(authority)
        assert restarted.retention_policy_id == "audit-admission-retention:indefinite:v1"
        assert (
            restarted.recover_all().store_health.status
            is AuditAdmissionStorageHealthStatus.HEALTHY
        )
        restarted.create_or_get_installation(
            recipe_execution_id=RecipeExecutionId("far-future-execution"),
            snapshot_digest=_digest("far-future-snapshot"),
        )

        replay = restarted.reserve(request)
        assert replay.decision is ReservationDecision.EXACT_REPLAY
        assert replay.replay_outcome == outcome
        assert restarted.finalization_effect_result(
            attempt_id,
            _REQUIRED_FINALIZATION_EFFECTS[0],
        ) == {"completed": True}
        with pytest.raises(
            AuditAdmissionStorageError,
            match="finalize-response-commit-mismatch",
        ):
            restarted.finalize_response(
                attempt_id,
                outcome,
                required_effect_names=(
                    *_REQUIRED_FINALIZATION_EFFECTS,
                    "pipeline_step_completed",
                ),
            )

    def test_restart_fails_closed_if_retained_commit_loses_an_acknowledgement(
        self,
        tmp_path: Path,
    ) -> None:
        authority = _authority(tmp_path)
        ledger = DefaultAuditAdmissionLedger(authority)
        attempt_id, _ = _published_attempt(ledger, tmp_path)
        _acknowledge_required_finalization_effects(ledger, attempt_id)
        ledger.finalize_response(
            attempt_id,
            AuditOutcome(
                status=AuditOutcomeStatus.PUBLISHED,
                attempt_id=attempt_id,
                verdict=AuditVerdict.GO,
                path=tmp_path / "authority.json",
                error=None,
                replay_response_json='{"audit_status":"EXACT_REPLAY"}',
            ),
            required_effect_names=_REQUIRED_FINALIZATION_EFFECTS,
        )
        with sqlite3.connect(authority.database_path) as connection:
            connection.execute(
                "DELETE FROM finalization_effects WHERE attempt_id = ? AND effect_name = ?",
                (attempt_id.value, _REQUIRED_FINALIZATION_EFFECTS[0]),
            )

        recovery = DefaultAuditAdmissionLedger(authority).recover_all()
        assert recovery.store_health.status is AuditAdmissionStorageHealthStatus.FAIL_CLOSED
        assert recovery.store_health.failure_reason is AuditAdmissionStorageFailureReason.INTEGRITY
        assert (
            recovery.store_health.reason_code == "response-commit-finalization-effects-incomplete"
        )


class TestRecovery:
    def test_recover_all_reports_healthy_and_open_attempts(self, tmp_path: Path) -> None:
        ledger = DefaultAuditAdmissionLedger(_authority(tmp_path))
        execution_id = RecipeExecutionId("exec-1")
        version = ledger.create_or_get_installation(
            recipe_execution_id=execution_id, snapshot_digest=_digest("s")
        )
        ledger.reserve(_reservation_request(tmp_path, execution_id, version))

        recovery = ledger.recover_all()
        assert recovery.store_health.status is AuditAdmissionStorageHealthStatus.HEALTHY
        assert execution_id in recovery.recovered_installations
        assert len(recovery.recovered_attempts) == 1

    def test_state_survives_a_fresh_ledger_instance_against_the_same_store(
        self, tmp_path: Path
    ) -> None:
        authority = _authority(tmp_path)
        ledger = DefaultAuditAdmissionLedger(authority)
        execution_id = RecipeExecutionId("exec-1")
        version = ledger.create_or_get_installation(
            recipe_execution_id=execution_id, snapshot_digest=_digest("s")
        )
        request = _reservation_request(tmp_path, execution_id, version)
        reserved = ledger.reserve(request)
        ledger.prepare(
            AuditPrepareRequest(
                attempt_id=reserved.attempt_id,
                installation_version=version,  # type: ignore[arg-type]
                semantic_digest=_digest("semantic"),
                accepted=True,
            )
        )
        head = _head(tmp_path, execution_id, _plan_set_id(reserved))
        ledger.commit_authority(
            AuditFinalCommitRequest(
                attempt_id=reserved.attempt_id,
                installation_version=version,  # type: ignore[arg-type]
                expected_head_digest=None,
                new_head=head,
                preflight_step_names=("make-plan",),
            )
        )

        restarted = DefaultAuditAdmissionLedger(authority)
        recovery = restarted.recover_all()
        assert recovery.store_health.status is AuditAdmissionStorageHealthStatus.HEALTHY
        current = restarted.current_head(
            recipe_execution_id=execution_id,
            cycle_id="cycle-1",
            scope_id="scope-1",
            part_id="part-1",
        )
        assert current is not None
        assert current.current_authority_digest == head.current_authority_digest


class TestIsolation:
    def test_two_ledgers_in_distinct_namespaces_do_not_share_state(self, tmp_path: Path) -> None:
        ledger_a = DefaultAuditAdmissionLedger(
            AuditAdmissionStoreAuthority(
                database_path=tmp_path / "a" / "ledger.sqlite3", expected_owner_id=os.getuid()
            )
        )
        ledger_b = DefaultAuditAdmissionLedger(
            AuditAdmissionStoreAuthority(
                database_path=tmp_path / "b" / "ledger.sqlite3", expected_owner_id=os.getuid()
            )
        )
        execution_id = RecipeExecutionId("exec-1")
        version_a = ledger_a.create_or_get_installation(
            recipe_execution_id=execution_id, snapshot_digest=_digest("s")
        )
        outcome = ledger_a.reserve(_reservation_request(tmp_path, execution_id, version_a))
        assert outcome.decision is ReservationDecision.DISPATCH_NEW

        with pytest.raises(ValueError, match="create_or_get_installation"):
            ledger_b.reserve(_reservation_request(tmp_path, execution_id, version_a))
