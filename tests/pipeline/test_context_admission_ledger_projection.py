"""Projection tests for the crash-safe context-admission ledger.

Part of the test split for issue #4606.
"""

from __future__ import annotations

import sqlite3
from dataclasses import replace
from pathlib import Path

import pytest

import autoskillit.core.types._type_context_admission_persistence as persistence_types
import autoskillit.pipeline.context_admission_ledger as ledger_module
from autoskillit.core import (
    ActiveContextAdmissionState,
    AdmissionDecisionKind,
    AdmissionEventId,
    AdmissionState,
    ContextAdmissionAccountingStatus,
    ContextAdmissionStorageFailureReason,
    ContextAdmissionStorageHealthStatus,
    MeasurementKind,
)
from autoskillit.pipeline.context_admission_ledger import (
    DefaultContextAdmissionLedger,
)
from tests.fixtures.context_admission import (
    accept_event,
    batch,
    dispatch_event,
    mark_indeterminate_event,
    occurrence,
    open_event,
    prepare_event,
    propose_event,
    reconcile_generation_event,
    release_non_admission_event,
    reserve_event,
    rollover_event,
    snapshot,
    stage_event,
    start_generation_event,
    stream_key,
)
from tests.pipeline._context_admission_ledger_helpers import (
    _authority,
)

pytestmark = [pytest.mark.layer("pipeline"), pytest.mark.small]


def test_recovery_and_inspection_hold_one_snapshot_across_projection_reads(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    authority = _authority(tmp_path)
    key = stream_key()
    assert (
        DefaultContextAdmissionLedger(authority).apply(key, open_event()).status
        is ContextAdmissionAccountingStatus.RECORDED
    )
    reader = DefaultContextAdmissionLedger(authority)
    statements: list[str] = []
    original_connect = reader._connect

    def traced_connect() -> sqlite3.Connection:
        connection = original_connect()
        connection.set_trace_callback(statements.append)
        return connection

    monkeypatch.setattr(reader, "_connect", traced_connect)

    assert reader.recover_all().status is ContextAdmissionStorageHealthStatus.HEALTHY

    def assert_uninterrupted_projection_reads(
        trace: list[str],
        *,
        stream_query: str,
        expect_commit: bool,
    ) -> None:
        begin_index = trace.index("BEGIN")
        read_indices = [
            next(index for index, statement in enumerate(trace) if query in statement)
            for query in (
                stream_query,
                "FROM journal_events",
                "FROM effect_outbox",
                "FROM shadow_decisions",
            )
        ]
        last_read_index = max(read_indices)
        assert begin_index < min(read_indices)
        assert not any(
            statement in {"BEGIN", "COMMIT", "ROLLBACK"}
            for statement in trace[begin_index + 1 : last_read_index + 1]
        )
        if expect_commit:
            assert trace.index("COMMIT", last_read_index + 1) > last_read_index

    assert_uninterrupted_projection_reads(
        statements,
        stream_query="FROM streams",
        expect_commit=True,
    )

    inspection_start = len(statements)
    assert reader.inspect_stream(key).health.status is ContextAdmissionStorageHealthStatus.HEALTHY
    inspection_statements = statements[inspection_start:]
    assert_uninterrupted_projection_reads(
        inspection_statements,
        stream_query="FROM streams WHERE stream_id",
        expect_commit=False,
    )


@pytest.mark.parametrize("row_limit", [3, 5])
def test_recovery_enforces_metadata_and_preflight_row_budgets(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    row_limit: int,
) -> None:
    authority = _authority(tmp_path)
    key = stream_key()
    assert (
        DefaultContextAdmissionLedger(authority).apply(key, open_event()).status
        is ContextAdmissionAccountingStatus.RECORDED
    )
    monkeypatch.setattr(ledger_module, "_MAX_RECOVERY_ROWS", row_limit)

    row_bounded = DefaultContextAdmissionLedger(authority)
    recovered = row_bounded.recover_all()

    assert recovered.status is ContextAdmissionStorageHealthStatus.FAIL_CLOSED
    assert recovered.store_health.failure_reason is ContextAdmissionStorageFailureReason.INTEGRITY
    assert recovered.store_health.reason_code == "recovery-read-limit-exceeded"


def test_recovery_enforces_sqlite_value_and_aggregate_byte_budgets(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    authority = _authority(tmp_path)
    key = stream_key()
    assert (
        DefaultContextAdmissionLedger(authority).apply(key, open_event()).status
        is ContextAdmissionAccountingStatus.RECORDED
    )

    monkeypatch.setattr(ledger_module, "_MAX_RECOVERY_BYTES", 1)
    byte_bounded = DefaultContextAdmissionLedger(authority)

    byte_result = byte_bounded.recover_all()

    assert byte_result.status is ContextAdmissionStorageHealthStatus.FAIL_CLOSED
    assert (
        byte_result.store_health.failure_reason is ContextAdmissionStorageFailureReason.INTEGRITY
    )
    assert byte_result.store_health.reason_code == "recovery-read-limit-exceeded"


def test_inspection_enforces_aggregate_read_budget(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ledger = DefaultContextAdmissionLedger(_authority(tmp_path))
    key = stream_key()
    assert ledger.apply(key, open_event()).status is ContextAdmissionAccountingStatus.RECORDED
    monkeypatch.setattr(ledger_module, "_MAX_RECOVERY_ROWS", 1)

    inspection = ledger.inspect_stream(key)

    assert inspection.health.status is ContextAdmissionStorageHealthStatus.FAIL_CLOSED
    assert inspection.health.failure_reason is ContextAdmissionStorageFailureReason.INTEGRITY
    assert inspection.health.reason_code == "inspection-read-limit-exceeded"


@pytest.mark.parametrize("operation", ["inspect", "recover"])
def test_persisted_sequence_bounds_do_not_drive_allocations(
    tmp_path: Path,
    operation: str,
) -> None:
    authority = _authority(tmp_path)
    key = stream_key()
    ledger = DefaultContextAdmissionLedger(authority)
    assert ledger.apply(key, open_event()).status is ContextAdmissionAccountingStatus.RECORDED
    connection = sqlite3.connect(authority.database_path)
    try:
        connection.execute(
            "UPDATE streams SET latest_journal_sequence = ?",
            ((2**63) - 1,),
        )
        connection.commit()
    finally:
        connection.close()

    if operation == "inspect":
        health = ledger.inspect_stream(key).health
    else:
        recovered = DefaultContextAdmissionLedger(authority)
        recovered.recover_all()
        health = recovered.stream_health(key)

    assert health.status is ContextAdmissionStorageHealthStatus.FAIL_CLOSED
    assert health.failure_reason is ContextAdmissionStorageFailureReason.REPLAY_MISMATCH
    assert health.reason_code == "journal-sequence-gap"


@pytest.mark.parametrize(
    ("corruption", "expected_reason"),
    [
        ("effect-ordinal", "effect-sequence-gap"),
        ("effect-payload", "journal-effects-mismatch"),
        ("prior-coordinate", "journal-prior-coordinate-mismatch"),
        ("result-coordinate", "journal-result-coordinate-mismatch"),
        ("shadow-content", "journal-shadow-mismatch"),
    ],
)
def test_recovery_rejects_projection_corruption_with_exact_reason(
    tmp_path: Path,
    corruption: str,
    expected_reason: str,
) -> None:
    authority = _authority(tmp_path)
    key = stream_key()
    ledger = DefaultContextAdmissionLedger(authority)
    opened = ledger.apply(key, open_event())
    assert opened.transition is not None
    assert opened.status is ContextAdmissionAccountingStatus.RECORDED
    occurrence_value = occurrence()
    proposed = ledger.apply(
        key,
        propose_event(opened.transition.next_state, occurrence_value),
    )
    assert proposed.transition is not None
    assert proposed.status is ContextAdmissionAccountingStatus.RECORDED
    batch_value = batch(occurrence_value)
    reserved = ledger.apply(
        key,
        reserve_event(
            proposed.transition.next_state,
            batch_value,
            occurrence_value,
        ),
    )
    assert reserved.transition is not None
    assert reserved.status is ContextAdmissionAccountingStatus.RECORDED
    released = ledger.apply(
        key,
        release_non_admission_event(
            reserved.transition.next_state,
            batch_value,
        ),
    )
    assert released.status is ContextAdmissionAccountingStatus.RECORDED
    connection = sqlite3.connect(authority.database_path)
    try:
        if corruption == "effect-ordinal":
            assert connection.execute(
                "SELECT COUNT(*) FROM effect_outbox WHERE journal_sequence = 3"
            ).fetchone()[0]
            connection.execute(
                """
                UPDATE effect_outbox
                SET effect_ordinal = 100
                WHERE journal_sequence = 3 AND effect_ordinal = 0
                """
            )
        elif corruption == "effect-payload":
            assert connection.execute(
                "SELECT COUNT(*) FROM effect_outbox WHERE journal_sequence = 4"
            ).fetchone()[0]
            connection.execute(
                """
                UPDATE effect_outbox
                SET effect_envelope = (
                    SELECT effect_envelope
                    FROM effect_outbox
                    WHERE journal_sequence = 4 AND effect_ordinal = 0
                )
                WHERE journal_sequence = 3 AND effect_ordinal = 0
                """
            )
        elif corruption == "prior-coordinate":
            connection.execute(
                """
                UPDATE journal_events
                SET prior_aggregate_revision = prior_aggregate_revision + 1
                WHERE journal_sequence = 2
                """
            )
        elif corruption == "result-coordinate":
            connection.execute(
                """
                UPDATE journal_events
                SET resulting_aggregate_revision = resulting_aggregate_revision + 1
                WHERE journal_sequence = 2
                """
            )
        else:
            encoded = bytes(
                connection.execute(
                    """
                    SELECT shadow_envelope
                    FROM shadow_decisions
                    WHERE journal_sequence = 1
                    """
                ).fetchone()[0]
            )
            envelope = persistence_types.decode_stored_context_admission_envelope(encoded)
            assert isinstance(
                envelope.payload,
                persistence_types.ShadowContextAdmissionRecord,
            )
            tampered = replace(
                envelope.payload,
                journal_sequence=envelope.payload.journal_sequence + 1,
            )
            connection.execute(
                """
                UPDATE shadow_decisions
                SET shadow_envelope = ?
                WHERE journal_sequence = 1
                """,
                (
                    persistence_types.encode_stored_context_admission_envelope(
                        persistence_types.make_stored_context_admission_envelope(tampered)
                    ),
                ),
            )
        connection.commit()
    finally:
        connection.close()

    recovered = DefaultContextAdmissionLedger(authority)
    result = recovered.recover_all()

    assert result.status is ContextAdmissionStorageHealthStatus.HEALTHY
    health = recovered.stream_health(key)
    assert health.status is ContextAdmissionStorageHealthStatus.FAIL_CLOSED
    assert health.failure_reason is ContextAdmissionStorageFailureReason.REPLAY_MISMATCH
    assert health.reason_code == expected_reason


def test_expired_event_exact_replay_revalidates_stored_decision(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    authority = _authority(tmp_path)
    key = stream_key()
    event = open_event()
    ledger = DefaultContextAdmissionLedger(authority)
    assert ledger.apply(key, event).status is ContextAdmissionAccountingStatus.RECORDED
    connection = sqlite3.connect(authority.database_path)
    try:
        encoded = bytes(
            connection.execute("SELECT decision_envelope FROM journal_events").fetchone()[0]
        )
        envelope = persistence_types.decode_stored_context_admission_envelope(encoded)
        assert isinstance(envelope.payload, persistence_types.AdmissionDecision)
        tampered = replace(envelope.payload, reason_code="validly-encoded-tamper")
        connection.execute(
            "UPDATE journal_events SET decision_envelope = ?",
            (
                persistence_types.encode_stored_context_admission_envelope(
                    persistence_types.make_stored_context_admission_envelope(tampered)
                ),
            ),
        )
        connection.commit()
    finally:
        connection.close()
    monkeypatch.setattr(ledger_module, "_state_retains_event", lambda *_args: False)

    replayed = ledger.apply(key, event)

    assert replayed.status is ContextAdmissionAccountingStatus.STORAGE_FAIL_CLOSED
    assert replayed.failure_reason is ContextAdmissionStorageFailureReason.REPLAY_MISMATCH
    assert replayed.reason_code == "journal-decision-mismatch"


def test_shadow_projection_preserves_exact_input_and_output_measurements(
    tmp_path: Path,
) -> None:
    ledger = DefaultContextAdmissionLedger(_authority(tmp_path))
    key = stream_key()
    occurrence_value = occurrence()
    batch_value = batch(occurrence_value)

    opened = ledger.apply(key, open_event())
    assert opened.transition is not None
    proposed = ledger.apply(
        key,
        propose_event(opened.transition.next_state, occurrence_value),
    )
    assert proposed.transition is not None
    reserved = ledger.reserve(
        key,
        reserve_event(
            proposed.transition.next_state,
            batch_value,
            occurrence_value,
            generation_allowance=15,
        ),
    )
    assert reserved.transition is not None
    prepared = ledger.apply(
        key,
        prepare_event(reserved.transition.next_state, batch_value),
    )
    assert prepared.transition is not None
    staged = ledger.apply(
        key,
        stage_event(prepared.transition.next_state, batch_value),
    )
    assert staged.transition is not None
    dispatched = ledger.apply(
        key,
        dispatch_event(staged.transition.next_state, batch_value),
    )
    assert dispatched.transition is not None
    generation_started = ledger.apply(
        key,
        start_generation_event(dispatched.transition.next_state, batch_value),
    )
    assert generation_started.transition is not None
    accepted = ledger.commit(
        key,
        accept_event(
            generation_started.transition.next_state,
            batch_value,
            exact_input_charge=9,
        ),
    )
    assert accepted.transition is not None
    reconciled = ledger.commit(
        key,
        reconcile_generation_event(
            accepted.transition.next_state,
            batch_value,
            exact_output_usage=7,
        ),
    )
    assert reconciled.transition is not None

    inspection = ledger.replay(key)
    assert inspection.latest_journal_sequence == 9
    assert tuple(record.journal_sequence for record in inspection.shadows) == tuple(range(1, 10))
    accepted_target = inspection.shadows[7].targets[0]
    assert accepted_target.exact_input_charge == 9
    assert accepted_target.measurement_kind is not None
    reconciled_target = inspection.shadows[8].targets[0]
    assert reconciled_target.exact_output_charge == 7
    assert reconciled_target.generation_allowance == 15
    assert reconciled_target.measurement_kind is MeasurementKind.PROVIDER_EXACT


def test_rollover_projection_retains_every_invalidated_target_and_replays(
    tmp_path: Path,
) -> None:
    authority = _authority(tmp_path)
    ledger = DefaultContextAdmissionLedger(authority)
    key = stream_key()
    occurrence_value = occurrence()
    batch_value = batch(occurrence_value)
    opened = ledger.apply(key, open_event())
    assert opened.transition is not None
    proposed = ledger.apply(
        key,
        propose_event(opened.transition.next_state, occurrence_value),
    )
    assert proposed.transition is not None
    reserved = ledger.reserve(
        key,
        reserve_event(
            proposed.transition.next_state,
            batch_value,
            occurrence_value,
            generation_allowance=15,
        ),
    )
    assert reserved.transition is not None
    rolled_over = ledger.apply(
        key,
        rollover_event(reserved.transition.next_state, batch_value),
    )
    assert rolled_over.transition is not None

    targets = ledger.inspect_stream(key).shadows[-1].targets
    assert tuple(target.target_id.value for target in targets) == (
        "batch-one",
        "generation-one",
    )
    assert targets[0].proposed_input_count == 10
    assert targets[1].generation_allowance == 15
    recovered = DefaultContextAdmissionLedger(authority).recover_all()
    assert recovered.status is ContextAdmissionStorageHealthStatus.HEALTHY
    assert recovered.unresolved_streams == ()


def test_exact_event_retry_returns_current_state_noop_without_append(
    tmp_path: Path,
) -> None:
    authority = _authority(tmp_path)
    ledger = DefaultContextAdmissionLedger(authority)
    key = stream_key()
    event = open_event()
    recorded = ledger.apply(key, event)
    assert recorded.transition is not None

    replayed = ledger.apply(key, event)

    assert replayed.status is ContextAdmissionAccountingStatus.EXACT_REPLAY
    assert replayed.journal_sequence == 1
    assert replayed.transition is not None
    assert replayed.transition.effects == ()
    assert replayed.transition.decision.kind is AdmissionDecisionKind.NOOP_IDEMPOTENT
    assert replayed.transition.next_state == recorded.transition.next_state
    connection = sqlite3.connect(authority.database_path)
    try:
        assert connection.execute("SELECT COUNT(*) FROM journal_events").fetchone() == (1,)
    finally:
        connection.close()


def test_changed_intent_under_existing_event_id_is_conflict_without_append(
    tmp_path: Path,
) -> None:
    authority = _authority(tmp_path)
    ledger = DefaultContextAdmissionLedger(authority)
    key = stream_key()
    event = open_event()
    assert ledger.apply(key, event).journal_sequence == 1
    changed = replace(event, snapshot=snapshot(remaining_count=30))

    result = ledger.apply(key, changed)

    assert result.status is ContextAdmissionAccountingStatus.SEMANTIC_REJECTION
    assert result.transition is not None
    assert result.transition.decision.kind is AdmissionDecisionKind.CONFLICT
    assert result.journal_sequence is None
    connection = sqlite3.connect(authority.database_path)
    try:
        assert connection.execute("SELECT COUNT(*) FROM journal_events").fetchone() == (1,)
    finally:
        connection.close()


def test_reservation_key_retry_appends_one_noop_then_exact_replays(
    tmp_path: Path,
) -> None:
    authority = _authority(tmp_path)
    ledger = DefaultContextAdmissionLedger(authority)
    key = stream_key()
    occurrence_value = occurrence()
    opened = ledger.apply(key, open_event())
    assert opened.transition is not None
    proposed = ledger.apply(
        key,
        propose_event(opened.transition.next_state, occurrence_value),
    )
    assert proposed.transition is not None
    original_event = reserve_event(
        proposed.transition.next_state,
        batch(occurrence_value),
        occurrence_value,
    )
    reserved = ledger.reserve(key, original_event)
    assert reserved.transition is not None

    retry_event = replace(
        original_event,
        event_id=AdmissionEventId("event-reserve-new-event-id"),
        expected_aggregate_revision=reserved.transition.next_state.aggregate_revision,
    )
    noop = ledger.reserve(key, retry_event)

    assert noop.status is ContextAdmissionAccountingStatus.RECORDED
    assert noop.transition is not None
    assert noop.transition.decision.kind is AdmissionDecisionKind.NOOP_IDEMPOTENT
    assert noop.transition.effects == ()
    assert noop.journal_sequence == 4
    exact = ledger.reserve(key, retry_event)
    assert exact.status is ContextAdmissionAccountingStatus.EXACT_REPLAY
    assert exact.journal_sequence == 4

    inspection = ledger.inspect_stream(key)
    assert inspection.latest_journal_sequence == 4
    assert inspection.state == noop.transition.next_state


def test_unretained_reservation_conflict_exact_replays_stored_decision(
    tmp_path: Path,
) -> None:
    authority = _authority(tmp_path)
    ledger = DefaultContextAdmissionLedger(authority)
    key = stream_key()
    occurrence_value = occurrence()
    opened = ledger.apply(key, open_event())
    assert opened.transition is not None
    proposed = ledger.apply(
        key,
        propose_event(opened.transition.next_state, occurrence_value),
    )
    assert proposed.transition is not None
    original_event = reserve_event(
        proposed.transition.next_state,
        batch(occurrence_value),
        occurrence_value,
    )
    reserved = ledger.reserve(key, original_event)
    assert reserved.transition is not None

    changed_reservation = replace(
        original_event.input_reservations[0],
        reserved_count=original_event.input_reservations[0].reserved_count + 1,
    )
    conflict_event = replace(
        original_event,
        event_id=AdmissionEventId("event-reserve-conflict"),
        expected_aggregate_revision=reserved.transition.next_state.aggregate_revision,
        input_reservations=(changed_reservation,),
    )
    conflict = ledger.reserve(key, conflict_event)

    assert conflict.status is ContextAdmissionAccountingStatus.SEMANTIC_REJECTION
    assert conflict.transition is not None
    assert conflict.transition.decision.kind is AdmissionDecisionKind.CONFLICT
    assert conflict.journal_sequence == 4
    replayed = ledger.reserve(key, conflict_event)
    assert replayed.status is ContextAdmissionAccountingStatus.EXACT_REPLAY
    assert replayed.journal_sequence == 4
    assert replayed.transition is not None
    assert replayed.transition.decision == conflict.transition.decision
    assert replayed.transition.effects == ()


def test_explicit_non_admission_witness_releases_reserved_capacity(
    tmp_path: Path,
) -> None:
    ledger = DefaultContextAdmissionLedger(_authority(tmp_path))
    key = stream_key()
    occurrence_value = occurrence()
    batch_value = batch(occurrence_value)
    opened = ledger.apply(key, open_event())
    assert opened.transition is not None
    proposed = ledger.apply(
        key,
        propose_event(opened.transition.next_state, occurrence_value),
    )
    assert proposed.transition is not None
    reserved = ledger.reserve(
        key,
        reserve_event(proposed.transition.next_state, batch_value, occurrence_value),
    )
    assert reserved.transition is not None

    released = ledger.release(
        key,
        release_non_admission_event(reserved.transition.next_state, batch_value),
    )

    assert released.status is ContextAdmissionAccountingStatus.RECORDED
    assert released.transition is not None
    batch_record = released.transition.next_state.batch_records[0]
    assert batch_record.state is AdmissionState.RELEASED
    assert released.transition.decision.available_ordinary_count == 40


def test_dispatched_indeterminate_work_remains_charged_across_recovery(
    tmp_path: Path,
) -> None:
    authority = _authority(tmp_path)
    ledger = DefaultContextAdmissionLedger(authority)
    key = stream_key()
    occurrence_value = occurrence()
    batch_value = batch(occurrence_value)
    opened = ledger.apply(key, open_event())
    assert opened.transition is not None
    proposed = ledger.apply(
        key,
        propose_event(opened.transition.next_state, occurrence_value),
    )
    assert proposed.transition is not None
    reserved = ledger.reserve(
        key,
        reserve_event(proposed.transition.next_state, batch_value, occurrence_value),
    )
    assert reserved.transition is not None
    prepared = ledger.apply(
        key,
        prepare_event(reserved.transition.next_state, batch_value),
    )
    assert prepared.transition is not None
    staged = ledger.apply(
        key,
        stage_event(prepared.transition.next_state, batch_value),
    )
    assert staged.transition is not None
    dispatched = ledger.apply(
        key,
        dispatch_event(staged.transition.next_state, batch_value),
    )
    assert dispatched.transition is not None
    marked = ledger.apply(
        key,
        mark_indeterminate_event(dispatched.transition.next_state, batch_value),
    )
    assert marked.status is ContextAdmissionAccountingStatus.RECONCILIATION_REQUIRED
    assert marked.transition is not None

    reopened = DefaultContextAdmissionLedger(authority)
    recovered = reopened.recover_all()
    state = reopened.inspect_stream(key).state

    assert recovered.unresolved_streams == (key,)
    assert isinstance(state, ActiveContextAdmissionState)
    assert state.batch_records[0].state is AdmissionState.INDETERMINATE
    assert sum(reservation.reserved_count for reservation in state.reservations) == 10


def test_rejected_indeterminate_event_is_reported_as_semantic_rejection(
    tmp_path: Path,
) -> None:
    ledger = DefaultContextAdmissionLedger(_authority(tmp_path))
    key = stream_key()
    opened = ledger.apply(key, open_event())
    assert opened.transition is not None
    batch_value = batch(occurrence())

    rejected = ledger.apply(
        key,
        mark_indeterminate_event(opened.transition.next_state, batch_value),
    )

    assert rejected.status is ContextAdmissionAccountingStatus.SEMANTIC_REJECTION
    assert rejected.transition is not None
    assert rejected.transition.decision.kind is AdmissionDecisionKind.WOULD_REJECT


@pytest.mark.parametrize(
    "fault_name",
    [
        "before_reduction",
        "after_reduction",
        "after_journal",
        "after_state_shadow",
        "before_commit",
    ],
)
def test_precommit_faults_roll_back_every_projection(
    tmp_path: Path,
    fault_name: str,
) -> None:
    authority = _authority(tmp_path)

    def inject(point: object) -> None:
        if getattr(point, "value") == fault_name:
            raise RuntimeError(fault_name)

    ledger = DefaultContextAdmissionLedger(authority, fault_callback=inject)
    with pytest.raises(RuntimeError, match=fault_name):
        ledger.apply(stream_key(), open_event())
    connection = sqlite3.connect(authority.database_path)
    try:
        assert connection.execute("SELECT COUNT(*) FROM streams").fetchone() == (0,)
        assert connection.execute("SELECT COUNT(*) FROM journal_events").fetchone() == (0,)
        assert connection.execute("SELECT COUNT(*) FROM effect_outbox").fetchone() == (0,)
        assert connection.execute("SELECT COUNT(*) FROM shadow_decisions").fetchone() == (0,)
    finally:
        connection.close()


def test_fault_checkpoints_follow_distinct_mutation_boundaries(tmp_path: Path) -> None:
    authority = _authority(tmp_path)
    connections: list[sqlite3.Connection] = []
    observed_changes: dict[str, int] = {}
    recording = False

    def connection_factory(*args: object, **kwargs: object) -> sqlite3.Connection:
        connection = sqlite3.connect(*args, **kwargs)  # type: ignore[arg-type]
        connections.append(connection)
        return connection

    def record(point: object) -> None:
        point_name = getattr(point, "value")
        if recording and point_name in {
            "after_journal",
            "during_effects",
            "after_state_shadow",
            "before_commit",
        }:
            observed_changes[point_name] = connections[-1].total_changes

    ledger = DefaultContextAdmissionLedger(
        authority,
        fault_callback=record,
        connection_factory=connection_factory,
    )
    key = stream_key()
    occurrence_value = occurrence()
    opened = ledger.apply(key, open_event())
    assert opened.transition is not None
    proposed = ledger.apply(
        key,
        propose_event(opened.transition.next_state, occurrence_value),
    )
    assert proposed.transition is not None
    batch_value = batch(occurrence_value)
    recording = True

    reserved = ledger.reserve(
        key,
        reserve_event(
            proposed.transition.next_state,
            batch_value,
            occurrence_value,
            generation_allowance=15,
        ),
    )

    assert reserved.transition is not None
    assert len(reserved.transition.effects) >= 2
    checkpoint_order = (
        "after_journal",
        "during_effects",
        "after_state_shadow",
        "before_commit",
    )
    assert tuple(observed_changes) == checkpoint_order
    assert tuple(observed_changes[name] for name in checkpoint_order) == tuple(
        sorted(set(observed_changes.values()))
    )


def test_postcommit_fault_has_unknown_outcome_but_exact_retry_finds_publication(
    tmp_path: Path,
) -> None:
    authority = _authority(tmp_path)

    def inject(point: object) -> None:
        if getattr(point, "value") == "after_commit":
            raise RuntimeError("after-commit")

    ledger = DefaultContextAdmissionLedger(authority, fault_callback=inject)
    event = open_event()
    with pytest.raises(RuntimeError, match="after-commit"):
        ledger.apply(stream_key(), event)

    replayed = ledger.apply(stream_key(), event)
    assert replayed.status is ContextAdmissionAccountingStatus.EXACT_REPLAY
    assert replayed.journal_sequence == 1
