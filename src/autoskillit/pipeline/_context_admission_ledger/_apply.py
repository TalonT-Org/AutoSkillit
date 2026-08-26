"""Apply transaction boundary — the mutation authority.

Owns the journal insertion, effect outbox insertion, shadow insertion, and
state-update CAS wrapped in a single ``BEGIN IMMEDIATE`` transaction. The
busy-retry ``_commit`` loop lives here. These methods are rebound onto
:class:`DefaultContextAdmissionLedger` from ``__init__.py``.

Wavefront 1 of #4667.
"""

from __future__ import annotations

import sqlite3
import time
from dataclasses import replace

from autoskillit.core import (
    AcceptInputEvent,
    AdmissionDecisionKind,
    AdmissionTransition,
    AuthorityUnavailableEvent,
    ContextAdmissionAccountingResult,
    ContextAdmissionAccountingStatus,
    ContextAdmissionEvent,
    ContextAdmissionState,
    ContextAdmissionStorageFailureReason,
    ContextAdmissionStorageHealthStatus,
    ContextAdmissionStreamHealth,
    ContextAdmissionStreamKey,
    ContextAdmissionValidationError,
    OpenEpochEvent,
    ReconcileGenerationEvent,
    ReleaseNonAdmissionEvent,
    ReserveRequestEvent,
    ResolveIndeterminateAcceptedEvent,
    ResolveIndeterminateNonAdmissionEvent,
    ResolveIndeterminateRollbackEvent,
    RollbackAdmissionEvent,
    context_admission_reducer_for_protocol,
)

from ._codec import (
    _decode_decision,
    _decode_event,
    _decode_state,
    _encode_value,
    _stream_key_bytes,
    _zero_state,
)
from ._projection import (
    _MAX_RECOVERY_BYTES,
    _MAX_RECOVERY_ROWS,
    _LedgerReadBudget,
    _recover_stream_projection,
    _stored_stream_health,
)
from ._shadow import _shadow_record
from ._state_queries import (
    _state_has_unresolved_work,
    _state_retains_event,
    _validate_event_stream_identity,
)
from ._status import (
    _SQLITE_BUSY_CODES,
    _SQLITE_RECOVERY_CODES,
    _accounting_status,
    _LedgerContended,
    _LedgerFaultPoint,
    _rollback,
    _sqlite_primary_code,
    _uninitialized_stream_result,
)
from ._storage import _LedgerOpenError

__all__ = [
    "apply",
    "reserve",
    "commit",
    "release",
    "_recover_sqlite_result",
    "_commit",
    "_persist_stream_failure",
    "_storage_failure_result",
]


def apply(
    self,
    stream_key: ContextAdmissionStreamKey,
    event: ContextAdmissionEvent,
) -> ContextAdmissionAccountingResult:
    with self._fence:
        if not self._recovered:
            self.recover_all()
        if not self._recovered:
            return ContextAdmissionAccountingResult(
                status=ContextAdmissionAccountingStatus.CONTENDED,
                stream_key=stream_key,
                reason_code="busy",
            )
        if self._store_health.status is ContextAdmissionStorageHealthStatus.FAIL_CLOSED:
            return self._storage_failure_result(stream_key)
        connection = None
        stream_id = _stream_key_bytes(stream_key)
        stream_exists = False
        current_state: ContextAdmissionState
        try:
            connection = self._connect()
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                """
                SELECT stream_key, state_envelope, aggregate_revision,
                       admission_sequence, latest_journal_sequence,
                       health_status, failure_reason, reason_code,
                       genesis_envelope
                FROM streams WHERE stream_id = ?
                """,
                (stream_id,),
            ).fetchone()
            if row is None:
                if not isinstance(event, OpenEpochEvent | AuthorityUnavailableEvent):
                    _rollback(connection)
                    return _uninitialized_stream_result(stream_key, event)
                current_state = _zero_state(event.protocol_version)
                genesis_envelope = _encode_value(
                    current_state,
                    protocol_version=event.protocol_version,
                )
                connection.execute(
                    """
                    INSERT INTO streams(
                        stream_id, stream_key, genesis_envelope, state_envelope,
                        aggregate_revision, admission_sequence,
                        latest_journal_sequence, health_status,
                        failure_reason, reason_code
                    ) VALUES (?, ?, ?, ?, 0, 0, 0, ?, NULL, NULL)
                    """,
                    (
                        stream_id,
                        stream_id,
                        genesis_envelope,
                        genesis_envelope,
                        ContextAdmissionStorageHealthStatus.HEALTHY.value,
                    ),
                )
                prior_revision = 0
                prior_sequence = 0
                prior_journal_sequence = 0
            else:
                stream_exists = True
                if bytes(row[0]) != stream_id:
                    raise _LedgerOpenError(
                        ContextAdmissionStorageFailureReason.IDENTITY_MISMATCH,
                        "stream-key-mismatch",
                    )
                persisted_health = _stored_stream_health(
                    stream_key,
                    row[5],
                    row[6],
                    row[7],
                    invalid_reason=ContextAdmissionStorageFailureReason.REPLAY_MISMATCH,
                )
                if persisted_health.status is ContextAdmissionStorageHealthStatus.FAIL_CLOSED:
                    _rollback(connection)
                    return ContextAdmissionAccountingResult(
                        status=ContextAdmissionAccountingStatus.STORAGE_FAIL_CLOSED,
                        stream_key=stream_key,
                        failure_reason=persisted_health.failure_reason,
                        reason_code=persisted_health.reason_code,
                    )
                if persisted_health.status is not ContextAdmissionStorageHealthStatus.HEALTHY:
                    raise _LedgerOpenError(
                        ContextAdmissionStorageFailureReason.REPLAY_MISMATCH,
                        "invalid-stream-health",
                    )
                try:
                    current_state = _decode_state(bytes(row[1]))
                except ContextAdmissionValidationError as exc:
                    raise _LedgerOpenError(
                        ContextAdmissionStorageFailureReason.REPLAY_MISMATCH,
                        "stored-state-decode-failed",
                    ) from exc
                prior_revision = int(row[2])
                prior_sequence = int(row[3])
                prior_journal_sequence = int(row[4])
                if (
                    current_state.aggregate_revision.value != prior_revision
                    or current_state.admission_sequence.value != prior_sequence
                ):
                    raise _LedgerOpenError(
                        ContextAdmissionStorageFailureReason.REPLAY_MISMATCH,
                        "materialized-state-coordinate-mismatch",
                    )
            existing = connection.execute(
                """
                SELECT journal_sequence, event_envelope, decision_envelope
                FROM journal_events
                WHERE stream_id = ? AND event_id = ?
                """,
                (stream_id, event.event_id.value),
            ).fetchone()
            reducer = context_admission_reducer_for_protocol(event.protocol_version)
            if current_state.protocol_version != event.protocol_version:
                raise _LedgerOpenError(
                    ContextAdmissionStorageFailureReason.UNSUPPORTED_PROTOCOL,
                    "stream-protocol-mismatch",
                )
            if existing is not None:
                try:
                    original_event = _decode_event(bytes(existing[1]))
                    original_decision = _decode_decision(bytes(existing[2]))
                except ContextAdmissionValidationError as exc:
                    raise _LedgerOpenError(
                        ContextAdmissionStorageFailureReason.REPLAY_MISMATCH,
                        "stored-publication-decode-failed",
                    ) from exc
                if original_event != event:
                    conflict = replace(
                        original_decision,
                        kind=AdmissionDecisionKind.CONFLICT,
                        reason_code="event-id-conflict",
                    )
                    transition = AdmissionTransition(
                        next_state=current_state,
                        decision=conflict,
                        effects=(),
                    )
                    _rollback(connection)
                    return ContextAdmissionAccountingResult(
                        status=ContextAdmissionAccountingStatus.SEMANTIC_REJECTION,
                        stream_key=stream_key,
                        transition=transition,
                        reason_code=conflict.reason_code,
                    )
                if _state_retains_event(current_state, event.event_id.value):
                    transition = reducer.reduce_transition(current_state, event)
                    if transition.effects:
                        raise _LedgerOpenError(
                            ContextAdmissionStorageFailureReason.REPLAY_MISMATCH,
                            "exact-replay-produced-effects",
                        )
                else:
                    _recover_stream_projection(
                        connection,
                        stream_id,
                        stream_key,
                        genesis_envelope=bytes(row[8]),
                        materialized_state_envelope=bytes(row[1]),
                        aggregate_revision=int(row[2]),
                        admission_sequence=int(row[3]),
                        latest_journal_sequence=int(row[4]),
                        read_budget=_LedgerReadBudget(
                            "exact-replay-read-limit-exceeded",
                            max_rows=_MAX_RECOVERY_ROWS,
                            max_bytes=_MAX_RECOVERY_BYTES,
                        ),
                    )
                    transition = AdmissionTransition(
                        next_state=current_state,
                        decision=original_decision,
                        effects=(),
                    )
                _rollback(connection)
                return ContextAdmissionAccountingResult(
                    status=ContextAdmissionAccountingStatus.EXACT_REPLAY,
                    stream_key=stream_key,
                    transition=transition,
                    journal_sequence=int(existing[0]),
                    reason_code=transition.decision.reason_code,
                )
            _validate_event_stream_identity(stream_key, event)
            self._fault_callback(_LedgerFaultPoint.BEFORE_REDUCTION)
            transition = reducer.reduce_transition(current_state, event)
            self._fault_callback(_LedgerFaultPoint.AFTER_REDUCTION)
            journal_sequence = prior_journal_sequence + 1
            connection.execute(
                """
                INSERT INTO journal_events(
                    stream_id, journal_sequence, event_id,
                    event_envelope, decision_envelope,
                    expected_revision, prior_aggregate_revision,
                    prior_admission_sequence, resulting_aggregate_revision,
                    resulting_admission_sequence
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    stream_id,
                    journal_sequence,
                    event.event_id.value,
                    _encode_value(event, protocol_version=event.protocol_version),
                    _encode_value(transition.decision, protocol_version=event.protocol_version),
                    event.expected_aggregate_revision.value,
                    prior_revision,
                    prior_sequence,
                    transition.next_state.aggregate_revision.value,
                    transition.next_state.admission_sequence.value,
                ),
            )
            self._fault_callback(_LedgerFaultPoint.AFTER_JOURNAL)
            for ordinal, effect in enumerate(transition.effects):
                connection.execute(
                    """
                    INSERT INTO effect_outbox(
                        stream_id, journal_sequence, effect_ordinal,
                        effect_envelope
                    ) VALUES (?, ?, ?, ?)
                    """,
                    (
                        stream_id,
                        journal_sequence,
                        ordinal,
                        _encode_value(effect, protocol_version=event.protocol_version),
                    ),
                )
                if ordinal == 0:
                    self._fault_callback(_LedgerFaultPoint.DURING_EFFECTS)
            shadow = _shadow_record(
                stream_key,
                current_state,
                event,
                transition,
                journal_sequence,
            )
            connection.execute(
                """
                INSERT INTO shadow_decisions(
                    stream_id, journal_sequence, shadow_envelope
                ) VALUES (?, ?, ?)
                """,
                (
                    stream_id,
                    journal_sequence,
                    _encode_value(shadow, protocol_version=event.protocol_version),
                ),
            )
            self._fault_callback(_LedgerFaultPoint.AFTER_STATE_SHADOW)
            cursor = connection.execute(
                """
                UPDATE streams
                SET state_envelope = ?, aggregate_revision = ?,
                    admission_sequence = ?, latest_journal_sequence = ?
                WHERE stream_id = ? AND aggregate_revision = ?
                  AND admission_sequence = ?
                  AND latest_journal_sequence = ?
                """,
                (
                    _encode_value(transition.next_state, protocol_version=event.protocol_version),
                    transition.next_state.aggregate_revision.value,
                    transition.next_state.admission_sequence.value,
                    journal_sequence,
                    stream_id,
                    prior_revision,
                    prior_sequence,
                    prior_journal_sequence,
                ),
            )
            if cursor.rowcount != 1:
                raise _LedgerOpenError(
                    ContextAdmissionStorageFailureReason.REPLAY_MISMATCH,
                    "stream-publication-cas-failed",
                )
            self._fault_callback(_LedgerFaultPoint.BEFORE_COMMIT)
            self._commit(connection)
            self._fault_callback(_LedgerFaultPoint.AFTER_COMMIT)
            self._stream_health[stream_key] = ContextAdmissionStreamHealth(
                stream_key,
                ContextAdmissionStorageHealthStatus.HEALTHY,
            )
            self._unresolved_streams.discard(stream_key)
            if _state_has_unresolved_work(transition.next_state):
                self._unresolved_streams.add(stream_key)
            return ContextAdmissionAccountingResult(
                status=_accounting_status(event, transition),
                stream_key=stream_key,
                transition=transition,
                journal_sequence=journal_sequence,
                reason_code=transition.decision.reason_code,
            )
        except _LedgerContended:
            if connection is not None:
                _rollback(connection)
            return ContextAdmissionAccountingResult(
                status=ContextAdmissionAccountingStatus.CONTENDED,
                stream_key=stream_key,
                reason_code="busy",
            )
        except _LedgerOpenError as exc:
            if connection is not None:
                _rollback(connection)
                if stream_exists and exc.reason in {
                    ContextAdmissionStorageFailureReason.IDENTITY_MISMATCH,
                    ContextAdmissionStorageFailureReason.REPLAY_MISMATCH,
                }:
                    persisted = self._persist_stream_failure(
                        connection,
                        stream_id,
                        stream_key,
                        exc.reason,
                        exc.reason_code,
                    )
                    if not persisted and self._store_health.status is not (
                        ContextAdmissionStorageHealthStatus.FAIL_CLOSED
                    ):
                        return ContextAdmissionAccountingResult(
                            status=ContextAdmissionAccountingStatus.CONTENDED,
                            stream_key=stream_key,
                            reason_code="busy",
                        )
            else:
                self._set_store_failure(exc.reason, exc.reason_code)
            return ContextAdmissionAccountingResult(
                status=ContextAdmissionAccountingStatus.STORAGE_FAIL_CLOSED,
                stream_key=stream_key,
                failure_reason=exc.reason,
                reason_code=exc.reason_code,
            )
        except ContextAdmissionValidationError:
            if connection is not None:
                _rollback(connection)
            return ContextAdmissionAccountingResult(
                status=ContextAdmissionAccountingStatus.STORAGE_FAIL_CLOSED,
                stream_key=stream_key,
                failure_reason=ContextAdmissionStorageFailureReason.UNSUPPORTED_PROTOCOL,
                reason_code="protocol-validation-failed",
            )
        except sqlite3.Error as exc:
            primary_code = _sqlite_primary_code(exc)
            if primary_code in _SQLITE_BUSY_CODES:
                if connection is not None:
                    _rollback(connection)
                return ContextAdmissionAccountingResult(
                    status=ContextAdmissionAccountingStatus.CONTENDED,
                    stream_key=stream_key,
                    reason_code="busy",
                )
            if connection is not None and primary_code in _SQLITE_RECOVERY_CODES:
                return self._recover_sqlite_result(
                    connection,
                    stream_key,
                    event,
                )
            if connection is not None:
                _rollback(connection)
            reason = (
                ContextAdmissionStorageFailureReason.INTEGRITY
                if primary_code in {sqlite3.SQLITE_CORRUPT, sqlite3.SQLITE_CONSTRAINT}
                else ContextAdmissionStorageFailureReason.IO
            )
            self._set_store_failure(reason, "sqlite-publication-failed")
            return self._storage_failure_result(stream_key)
        except BaseException:
            if connection is not None:
                _rollback(connection)
            raise
        finally:
            if connection is not None:
                connection.close()


def _recover_sqlite_result(
    self,
    connection: sqlite3.Connection,
    stream_key: ContextAdmissionStreamKey,
    event: ContextAdmissionEvent,
) -> ContextAdmissionAccountingResult:
    _rollback(connection)
    connection.close()
    self._recovered = False
    from autoskillit.core import (
        ContextAdmissionStorageHealthStatus,
        ContextAdmissionStoreHealth,
    )

    self._store_health = ContextAdmissionStoreHealth(
        ContextAdmissionStorageHealthStatus.UNINITIALIZED
    )
    self._stream_health.clear()
    self._unresolved_streams.clear()
    recovery = self.recover_all()
    if recovery.status is ContextAdmissionStorageHealthStatus.FAIL_CLOSED:
        return self._storage_failure_result(stream_key)
    if not self._recovered:
        return ContextAdmissionAccountingResult(
            status=ContextAdmissionAccountingStatus.CONTENDED,
            stream_key=stream_key,
            reason_code="busy",
        )
    health = self.stream_health(stream_key)
    if health.status is ContextAdmissionStorageHealthStatus.FAIL_CLOSED:
        return ContextAdmissionAccountingResult(
            status=ContextAdmissionAccountingStatus.STORAGE_FAIL_CLOSED,
            stream_key=stream_key,
            failure_reason=health.failure_reason,
            reason_code=health.reason_code,
        )
    inspection = self.inspect_stream(stream_key)
    if inspection.health.status is ContextAdmissionStorageHealthStatus.FAIL_CLOSED:
        return ContextAdmissionAccountingResult(
            status=ContextAdmissionAccountingStatus.STORAGE_FAIL_CLOSED,
            stream_key=stream_key,
            failure_reason=inspection.health.failure_reason,
            reason_code=inspection.health.reason_code,
        )
    if inspection.health.status is ContextAdmissionStorageHealthStatus.UNINITIALIZED:
        return ContextAdmissionAccountingResult(
            status=ContextAdmissionAccountingStatus.CONTENDED,
            stream_key=stream_key,
            reason_code="busy",
        )
    if any(stored_event.event_id == event.event_id for stored_event in inspection.events):
        return self.apply(stream_key, event)
    self._set_store_failure(
        ContextAdmissionStorageFailureReason.AMBIGUOUS_RECOVERY,
        "sqlite-publication-absent",
    )
    return self._storage_failure_result(stream_key)


def reserve(
    self,
    stream_key: ContextAdmissionStreamKey,
    event: ContextAdmissionEvent,
) -> ContextAdmissionAccountingResult:
    if not isinstance(event, ReserveRequestEvent):
        raise TypeError("reserve_requires_reserve_request_event")
    return self.apply(stream_key, event)


def commit(
    self,
    stream_key: ContextAdmissionStreamKey,
    event: ContextAdmissionEvent,
) -> ContextAdmissionAccountingResult:
    if not isinstance(
        event,
        AcceptInputEvent | ResolveIndeterminateAcceptedEvent | ReconcileGenerationEvent,
    ):
        raise TypeError("commit_requires_exact_acceptance_event")
    return self.apply(stream_key, event)


def release(
    self,
    stream_key: ContextAdmissionStreamKey,
    event: ContextAdmissionEvent,
) -> ContextAdmissionAccountingResult:
    if not isinstance(
        event,
        ReleaseNonAdmissionEvent
        | RollbackAdmissionEvent
        | ResolveIndeterminateNonAdmissionEvent
        | ResolveIndeterminateRollbackEvent,
    ):
        raise TypeError("release_requires_witnessed_release_event")
    return self.apply(stream_key, event)


def _commit(self, connection: sqlite3.Connection) -> None:
    deadline = time.monotonic() + (self._busy_timeout_ms / 1_000)
    while True:
        try:
            connection.execute("COMMIT")
            return
        except sqlite3.Error as exc:
            if _sqlite_primary_code(exc) not in _SQLITE_BUSY_CODES:
                raise
            if time.monotonic() >= deadline:
                raise _LedgerContended from exc
            time.sleep(min(0.005, max(0.0, deadline - time.monotonic())))


def _persist_stream_failure(
    self,
    connection: sqlite3.Connection,
    stream_id: bytes,
    stream_key: ContextAdmissionStreamKey,
    reason: ContextAdmissionStorageFailureReason,
    reason_code: str,
) -> bool:
    try:
        connection.execute("BEGIN IMMEDIATE")
        connection.execute(
            """
            UPDATE streams
            SET health_status = ?, failure_reason = ?, reason_code = ?
            WHERE stream_id = ?
            """,
            (
                ContextAdmissionStorageHealthStatus.FAIL_CLOSED.value,
                reason.value,
                reason_code,
                stream_id,
            ),
        )
        connection.execute("COMMIT")
    except sqlite3.Error as exc:
        _rollback(connection)
        if _sqlite_primary_code(exc) in _SQLITE_BUSY_CODES:
            return False
        self._set_store_failure(
            ContextAdmissionStorageFailureReason.IO,
            "stream-health-persistence-failed",
        )
        return False
    self._stream_health[stream_key] = ContextAdmissionStreamHealth(
        stream_key,
        ContextAdmissionStorageHealthStatus.FAIL_CLOSED,
        failure_reason=reason,
        reason_code=reason_code,
    )
    self._unresolved_streams.discard(stream_key)
    return True


def _storage_failure_result(
    self,
    stream_key: ContextAdmissionStreamKey,
) -> ContextAdmissionAccountingResult:
    return ContextAdmissionAccountingResult(
        status=ContextAdmissionAccountingStatus.STORAGE_FAIL_CLOSED,
        stream_key=stream_key,
        failure_reason=(
            self._store_health.failure_reason or ContextAdmissionStorageFailureReason.CONFIGURATION
        ),
        reason_code=self._store_health.reason_code or "store-unavailable",
    )
