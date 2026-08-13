"""Effect-provenance contracts for fleet dispatch retry safety."""

from __future__ import annotations

import json

import pytest

from autoskillit.core import FleetErrorCode, ProcessCleanupResult
from autoskillit.fleet import (
    FLEET_STATE_SCHEMA_VERSION,
    DispatchAggregatePhase,
    DispatchCompleted,
    DispatchEffectName,
    DispatchEffectPhase,
    DispatchEffectProvenance,
    DispatchProvenanceTracker,
    DispatchRecord,
    DispatchRejected,
    DispatchRetryDisposition,
    DispatchStatus,
)

pytestmark = [
    pytest.mark.layer("fleet"),
    pytest.mark.small,
    pytest.mark.feature("fleet"),
]


def test_not_started_provenance_alone_allows_fresh_dispatch() -> None:
    tracker = DispatchProvenanceTracker(operation_id="operation-1")

    snapshot = tracker.snapshot()

    assert snapshot.aggregate_phase is DispatchAggregatePhase.NOT_STARTED
    assert snapshot.retry_disposition is DispatchRetryDisposition.FRESH_DISPATCH_ALLOWED


def test_confirmed_spawn_requires_identity_resume() -> None:
    tracker = DispatchProvenanceTracker(operation_id="operation-2")
    tracker.start(DispatchEffectName.PROCESS_SPAWN, identities={"dispatch_id": "dispatch-1"})
    tracker.confirm(
        DispatchEffectName.PROCESS_SPAWN,
        receipt="executor callback",
        identities={"dispatch_id": "dispatch-1", "pid": 123},
    )

    snapshot = tracker.snapshot()

    assert snapshot.aggregate_phase is DispatchAggregatePhase.STARTED
    assert snapshot.retry_disposition is DispatchRetryDisposition.RESUME_BY_IDENTITY
    assert snapshot.effects[0].phase is DispatchEffectPhase.CONFIRMED


def test_started_unconfirmed_effect_requires_reconciliation() -> None:
    tracker = DispatchProvenanceTracker(operation_id="operation-3")
    tracker.start(DispatchEffectName.PROCESS_SPAWN, identities={"dispatch_id": "dispatch-2"})

    snapshot = tracker.snapshot()

    assert snapshot.aggregate_phase is DispatchAggregatePhase.UNKNOWN
    assert snapshot.retry_disposition is DispatchRetryDisposition.RECONCILE_REQUIRED


def test_repeated_start_preserves_original_journal_entry() -> None:
    tracker = DispatchProvenanceTracker(operation_id="operation-repeated-start")
    tracker.start(DispatchEffectName.PROCESS_SPAWN, identities={"dispatch_id": "dispatch-1"})

    tracker.start(DispatchEffectName.PROCESS_SPAWN, identities={"dispatch_id": "dispatch-2"})

    effect = tracker.snapshot().effects[0]
    assert effect.phase is DispatchEffectPhase.STARTED
    assert dict(effect.known_downstream_identities) == {"dispatch_id": "dispatch-1"}


def test_ambiguity_preserves_non_retry_relevant_effect_metadata() -> None:
    tracker = DispatchProvenanceTracker(operation_id="operation-non-retry-ambiguous")
    tracker.start(
        DispatchEffectName.REQUESTED_RESUME_BINDING,
        retry_relevant=False,
        identities={"session_id": "session-1"},
    )

    tracker.mark_ambiguous(
        DispatchEffectName.REQUESTED_RESUME_BINDING,
        evidence="resume binding outcome unavailable",
    )

    effect = tracker.snapshot().effects[0]
    assert effect.phase is DispatchEffectPhase.STARTED
    assert effect.retry_relevant is False
    assert effect.ambiguity == "resume binding outcome unavailable"


def test_confirm_requires_started_effect() -> None:
    tracker = DispatchProvenanceTracker(operation_id="operation-missing-start")

    with pytest.raises(ValueError, match="was not started"):
        tracker.confirm(
            DispatchEffectName.PROCESS_SPAWN,
            receipt="executor callback",
        )


def test_local_cleanup_does_not_erase_confirmed_spawn() -> None:
    tracker = DispatchProvenanceTracker(operation_id="operation-4")
    tracker.start(
        DispatchEffectName.PROCESS_SPAWN,
        identities={"dispatch_id": "dispatch-3"},
    )
    tracker.confirm(
        DispatchEffectName.PROCESS_SPAWN,
        receipt="executor callback",
        identities={"dispatch_id": "dispatch-3", "pid": 456},
    )
    tracker.record_local_cleanup(
        ProcessCleanupResult(
            root_pid=456,
            process_identities=((456, 100.5),),
            terminated_pids=(456,),
            observation_complete=True,
        )
    )

    snapshot = tracker.snapshot()

    assert snapshot.local_cleanup is not None
    assert snapshot.local_cleanup.complete is True
    assert snapshot.aggregate_phase is DispatchAggregatePhase.STARTED
    assert snapshot.retry_disposition is DispatchRetryDisposition.RESUME_BY_IDENTITY


def test_commit_dominates_confirmed_effect_history() -> None:
    tracker = DispatchProvenanceTracker(operation_id="operation-5")
    tracker.start(
        DispatchEffectName.PROCESS_SPAWN,
        identities={"dispatch_id": "dispatch-4"},
    )
    tracker.confirm(
        DispatchEffectName.PROCESS_SPAWN,
        receipt="executor callback",
        identities={"dispatch_id": "dispatch-4"},
    )
    tracker.start(
        DispatchEffectName.COMMIT,
        identities={"dispatch_id": "dispatch-4"},
    )
    tracker.confirm(
        DispatchEffectName.COMMIT,
        receipt="terminal result",
        identities={"dispatch_id": "dispatch-4"},
    )

    snapshot = tracker.snapshot()

    assert snapshot.aggregate_phase is DispatchAggregatePhase.COMMITTED
    assert snapshot.retry_disposition is DispatchRetryDisposition.RESUME_BY_IDENTITY


def test_every_dispatch_envelope_carries_provenance() -> None:
    tracker = DispatchProvenanceTracker(operation_id="operation-6")
    rejected = DispatchRejected(
        error_code=FleetErrorCode.FLEET_ACQUIRE_TIMEOUT,
        message="busy",
        effect_provenance=tracker.snapshot(),
    )
    completed = DispatchCompleted(
        success=False,
        dispatch_status=DispatchStatus.FAILURE,
        dispatch_id="dispatch-5",
        dispatched_session_id="session-5",
        reason="failed",
        effect_provenance=tracker.snapshot(),
    )

    assert '"operation_id": "operation-6"' in rejected.to_envelope()
    assert '"operation_id": "operation-6"' in completed.to_envelope()


def test_failed_completion_preserves_domain_reason_in_error_field() -> None:
    completed = DispatchCompleted(
        success=False,
        dispatch_status=DispatchStatus.FAILURE,
        dispatch_id="dispatch-domain-failure",
        dispatched_session_id="session-domain-failure",
        reason="domain_validation_failed",
        effect_provenance=DispatchEffectProvenance(operation_id="operation-domain-failure"),
    )

    envelope = json.loads(completed.to_envelope())

    assert envelope["error"] == "domain_validation_failed"
    assert envelope["user_visible_message"] == "domain_validation_failed"


def test_outcome_constructors_require_provenance() -> None:
    with pytest.raises(TypeError, match="effect_provenance"):
        DispatchRejected(
            error_code=FleetErrorCode.FLEET_ACQUIRE_TIMEOUT,
            message="busy",
        )

    with pytest.raises(TypeError, match="effect_provenance"):
        DispatchCompleted(
            success=False,
            dispatch_status=DispatchStatus.FAILURE,
            dispatch_id="dispatch-missing-provenance",
            dispatched_session_id="",
            reason="failed",
        )


def test_dispatch_record_persists_provenance_in_current_schema() -> None:
    tracker = DispatchProvenanceTracker(operation_id="operation-7")
    tracker.start(
        DispatchEffectName.DISPATCH_ALLOCATION,
        identities={"dispatch_id": "dispatch-7"},
    )
    tracker.confirm(
        DispatchEffectName.DISPATCH_ALLOCATION,
        receipt="state identity",
        identities={"dispatch_id": "dispatch-7"},
    )
    record = DispatchRecord(
        name="dispatch",
        dispatch_id="dispatch-7",
        effect_provenance=tracker.snapshot().to_dict(),
    )

    restored = DispatchRecord.from_dict(record.to_dict())

    assert FLEET_STATE_SCHEMA_VERSION == 12
    assert restored.effect_provenance["operation_id"] == "operation-7"
    assert restored.effect_provenance["retry_disposition"] == "resume_by_identity"


def test_dispatch_record_normalizes_legacy_cleanup_evidence_fail_closed() -> None:
    raw = DispatchRecord(
        name="dispatch",
        effect_provenance={
            "operation_id": "operation-legacy",
            "local_cleanup": {
                "root_pid": 42,
                "survivor_pids": [],
                "access_denied_pids": [],
                "complete": True,
            },
        },
    ).to_dict()

    restored = DispatchRecord.from_dict(raw)

    cleanup = restored.effect_provenance["local_cleanup"]
    assert cleanup["observation_complete"] is False
    assert cleanup["complete"] is False


def test_dispatch_record_preserves_current_cleanup_evidence() -> None:
    cleanup = ProcessCleanupResult(root_pid=42, observation_complete=True).to_dict()
    raw = DispatchRecord(
        name="dispatch",
        effect_provenance={"operation_id": "operation-current", "local_cleanup": cleanup},
    ).to_dict()

    restored = DispatchRecord.from_dict(raw)

    assert restored.effect_provenance["local_cleanup"] == cleanup
