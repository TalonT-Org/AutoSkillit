"""Crash-safe audit-admission ledger tests."""

from __future__ import annotations

import hashlib
import os
import threading
from pathlib import Path

import pytest

from autoskillit.core import (
    ArtifactRef,
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
    RecipeExecutionId,
    ReservationDecision,
)
from autoskillit.pipeline.audit_admission_ledger import DefaultAuditAdmissionLedger

pytestmark = [pytest.mark.layer("pipeline"), pytest.mark.medium]


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

    def test_reserve_without_installation_raises(self, tmp_path: Path) -> None:
        ledger = DefaultAuditAdmissionLedger(_authority(tmp_path))
        execution_id = RecipeExecutionId("exec-1")
        from autoskillit.core import InstallationVersion

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
        )
        ledger.finalize_response(reserved.attempt_id, outcome)
        # Idempotent re-finalize with the identical outcome is a no-op, not an error.
        ledger.finalize_response(reserved.attempt_id, outcome)

        replay = ledger.reserve(request)
        assert replay.decision is ReservationDecision.EXACT_REPLAY
        assert replay.replay_outcome == outcome

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
        )
        ledger.finalize_response(reserved.attempt_id, first_outcome)
        conflicting_outcome = AuditOutcome(
            status=AuditOutcomeStatus.PUBLISHED,
            attempt_id=reserved.attempt_id,
            verdict=AuditVerdict.GO,
            path=tmp_path / "different-authority.json",
            error=None,
        )
        with pytest.raises(Exception, match="finalize-response-outcome-mismatch"):
            ledger.finalize_response(reserved.attempt_id, conflicting_outcome)


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
