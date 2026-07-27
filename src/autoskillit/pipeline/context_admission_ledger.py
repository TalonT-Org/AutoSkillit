"""Crash-safe SQLite storage for shadow context-admission accounting.

The ledger supports local filesystems whose SQLite VFS honors locking and sync
semantics. Network filesystems are intentionally outside the C2 durability
contract.
"""

from __future__ import annotations

import json
import os
import secrets
import sqlite3
import stat
import threading
import time
from collections.abc import Callable
from dataclasses import fields, is_dataclass, replace
from enum import StrEnum
from pathlib import Path
from typing import Any, Final, assert_never, cast, get_args

from autoskillit.core import (
    CONTEXT_ADMISSION_ENCODING_VERSION,
    CONTEXT_ADMISSION_PROTOCOL_VERSION,
    AcceptInputEvent,
    ActiveContextAdmissionState,
    AdmissionBatch,
    AdmissionBatchId,
    AdmissionBatchRecord,
    AdmissionDecision,
    AdmissionDecisionKind,
    AdmissionEffect,
    AdmissionOccurrenceId,
    AdmissionReservation,
    AdmissionSequence,
    AdmissionState,
    AdmissionTransition,
    AggregateRevision,
    AuthorityUnavailableEvent,
    ContextAdmissionAccountingResult,
    ContextAdmissionAccountingStatus,
    ContextAdmissionEvent,
    ContextAdmissionInspectionResult,
    ContextAdmissionRecoveryResult,
    ContextAdmissionState,
    ContextAdmissionStorageFailureReason,
    ContextAdmissionStorageHealthStatus,
    ContextAdmissionStoreAuthority,
    ContextAdmissionStoreHealth,
    ContextAdmissionStreamHealth,
    ContextAdmissionStreamKey,
    ContextAdmissionValidationError,
    ContextLineage,
    DispatchRequestEvent,
    DurableContextAdmissionPayload,
    ExpireIdempotencyKeyEvent,
    GenerationReservationId,
    GenerationReservationRecord,
    GenerationState,
    MarkGenerationIndeterminateEvent,
    MarkIndeterminateEvent,
    MeasurementKind,
    OpenEpochEvent,
    PrepareBatchEvent,
    ProposeOccurrenceEvent,
    ReconcileGenerationEvent,
    ReleaseNonAdmissionEvent,
    RequestReconciliationEvent,
    ReserveRequestEvent,
    ResolveIndeterminateAcceptedEvent,
    ResolveIndeterminateNonAdmissionEvent,
    ResolveIndeterminateRollbackEvent,
    RollbackAdmissionEvent,
    RolloverEpochEvent,
    ShadowContextAdmissionRecord,
    ShadowContextAdmissionTargetRecord,
    StageHistoryEvent,
    StartGenerationEvent,
    UninitializedContextAdmissionState,
    context_admission_reducer_for_protocol,
    decode_stored_context_admission_envelope,
    encode_stored_context_admission_envelope,
    make_stored_context_admission_envelope,
)

from ._context_admission_storage import (
    SCHEMA_SQL as _SCHEMA_SQL,
)
from ._context_admission_storage import (
    _LedgerOpenError,
    _LedgerReadBudget,
    _preflight_storage_routes,
    _read_bounded_rows,
    reconcile_initialization_links,
)
from ._context_admission_storage import (
    fsync_directory as _fsync_directory,
)
from ._context_admission_storage import (
    fsync_file as _fsync_file,
)
from ._context_admission_storage import (
    private_file_identity as _read_private_file_identity,
)
from ._context_admission_storage import (
    unlink_initialization_artifact as _unlink_initialization_artifact,
)

_SCHEMA_VERSION: Final = 1
_DATABASE_MODE: Final = 0o600
_DIRECTORY_MODE: Final = 0o700
_SQLITE_PRIMARY_MASK: Final = 0xFF
_SQLITE_BUSY_CODES: Final = frozenset({sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED})
_SQLITE_RECOVERY_CODES: Final = frozenset(
    {
        sqlite3.SQLITE_FULL,
        sqlite3.SQLITE_IOERR,
        sqlite3.SQLITE_INTERRUPT,
        sqlite3.SQLITE_NOMEM,
    }
)
_MAX_STREAM_KEY_BYTES: Final = 16 * 1024
_MAX_STREAM_KEY_JSON_NESTING: Final = 16
_MAX_RECOVERY_ROWS: Final = 100_000
_MAX_RECOVERY_BYTES: Final = 256 * 1024 * 1024
_EVENT_TYPES: Final = get_args(ContextAdmissionEvent)
_EFFECT_TYPES: Final = get_args(AdmissionEffect)
_STATE_TYPES: Final = get_args(ContextAdmissionState)


class _LedgerFaultPoint(StrEnum):
    BEFORE_REDUCTION = "before_reduction"
    AFTER_REDUCTION = "after_reduction"
    AFTER_JOURNAL = "after_journal"
    DURING_EFFECTS = "during_effects"
    AFTER_STATE_SHADOW = "after_state_shadow"
    BEFORE_COMMIT = "before_commit"
    AFTER_COMMIT = "after_commit"


class _LedgerContended(RuntimeError):
    pass


class DefaultContextAdmissionLedger:
    """SQLite-backed context-admission journal and verified projections."""

    def __init__(
        self,
        authority: ContextAdmissionStoreAuthority,
        *,
        busy_timeout_ms: int = 50,
        fault_callback: Callable[[_LedgerFaultPoint], None] | None = None,
        connection_factory: Callable[..., sqlite3.Connection] = sqlite3.connect,
    ) -> None:
        if (
            isinstance(busy_timeout_ms, bool)
            or not isinstance(busy_timeout_ms, int)
            or not 0 <= busy_timeout_ms <= 5_000
        ):
            raise ValueError("invalid_context_admission_busy_timeout")
        self._authority = authority
        self._path = authority.database_path
        self._busy_timeout_ms = busy_timeout_ms
        self._fault_callback = fault_callback or _ignore_fault
        self._connection_factory = connection_factory
        self._fence = threading.RLock()
        self._recovered = False
        self._store_health = ContextAdmissionStoreHealth(
            ContextAdmissionStorageHealthStatus.UNINITIALIZED
        )
        self._stream_health: dict[
            ContextAdmissionStreamKey,
            ContextAdmissionStreamHealth,
        ] = {}
        self._unresolved_streams: set[ContextAdmissionStreamKey] = set()

    @property
    def database_path(self) -> Path:
        """Return the configured path for diagnostics that already hold authority."""
        return self._path

    def store_health(self) -> ContextAdmissionStoreHealth:
        with self._fence:
            return self._store_health

    def stream_health(
        self,
        stream_key: ContextAdmissionStreamKey,
    ) -> ContextAdmissionStreamHealth:
        with self._fence:
            return self._stream_health.get(
                stream_key,
                ContextAdmissionStreamHealth(
                    stream_key,
                    ContextAdmissionStorageHealthStatus.UNINITIALIZED,
                ),
            )

    def apply(
        self,
        stream_key: ContextAdmissionStreamKey,
        event: ContextAdmissionEvent,
    ) -> ContextAdmissionAccountingResult:
        """Reduce and publish one event in a single immediate transaction."""
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
            connection: sqlite3.Connection | None = None
            stream_id = _stream_key_bytes(stream_key)
            stream_exists = False
            try:
                connection = self._connect()
                connection.execute("BEGIN IMMEDIATE")
                row = connection.execute(
                    """
                    SELECT stream_key, state_envelope, aggregate_revision,
                           admission_sequence, latest_journal_sequence,
                           health_status, failure_reason, reason_code
                    FROM streams WHERE stream_id = ?
                    """,
                    (stream_id,),
                ).fetchone()
                current_state: ContextAdmissionState
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
                    try:
                        persisted_health = _stored_stream_health(
                            stream_key,
                            row[5],
                            row[6],
                            row[7],
                        )
                    except (ContextAdmissionValidationError, ValueError) as exc:
                        raise _LedgerOpenError(
                            ContextAdmissionStorageFailureReason.REPLAY_MISMATCH,
                            "invalid-stream-health",
                        ) from exc
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
                        _encode_value(
                            event,
                            protocol_version=event.protocol_version,
                        ),
                        _encode_value(
                            transition.decision,
                            protocol_version=event.protocol_version,
                        ),
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
                            _encode_value(
                                effect,
                                protocol_version=event.protocol_version,
                            ),
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
                        _encode_value(
                            shadow,
                            protocol_version=event.protocol_version,
                        ),
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
                        _encode_value(
                            transition.next_state,
                            protocol_version=event.protocol_version,
                        ),
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
                health = ContextAdmissionStreamHealth(
                    stream_key,
                    ContextAdmissionStorageHealthStatus.HEALTHY,
                )
                self._stream_health[stream_key] = health
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
        event: ReserveRequestEvent,
    ) -> ContextAdmissionAccountingResult:
        if not isinstance(event, ReserveRequestEvent):
            raise TypeError("reserve_requires_reserve_request_event")
        return self.apply(stream_key, event)

    def commit(
        self,
        stream_key: ContextAdmissionStreamKey,
        event: (AcceptInputEvent | ResolveIndeterminateAcceptedEvent | ReconcileGenerationEvent),
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
        event: (
            ReleaseNonAdmissionEvent
            | RollbackAdmissionEvent
            | ResolveIndeterminateNonAdmissionEvent
            | ResolveIndeterminateRollbackEvent
        ),
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
        return True

    def _storage_failure_result(
        self,
        stream_key: ContextAdmissionStreamKey,
    ) -> ContextAdmissionAccountingResult:
        return ContextAdmissionAccountingResult(
            status=ContextAdmissionAccountingStatus.STORAGE_FAIL_CLOSED,
            stream_key=stream_key,
            failure_reason=(
                self._store_health.failure_reason
                or ContextAdmissionStorageFailureReason.CONFIGURATION
            ),
            reason_code=self._store_health.reason_code or "store-unavailable",
        )

    def recover(
        self,
        stream_key: ContextAdmissionStreamKey,
    ) -> ContextAdmissionRecoveryResult:
        result = self.recover_all()
        if result.status is ContextAdmissionStorageHealthStatus.FAIL_CLOSED:
            return result
        with self._fence:
            health = self.stream_health(stream_key)
            stream_healths = (
                (health,)
                if health.status is not ContextAdmissionStorageHealthStatus.UNINITIALIZED
                else ()
            )
            return ContextAdmissionRecoveryResult(
                status=result.status,
                store_health=result.store_health,
                stream_healths=stream_healths,
                recovered_streams=(
                    (stream_key,)
                    if health.status is ContextAdmissionStorageHealthStatus.HEALTHY
                    else ()
                ),
                unresolved_streams=(
                    (stream_key,) if stream_key in self._unresolved_streams else ()
                ),
            )

    def recover_all(self) -> ContextAdmissionRecoveryResult:
        with self._fence:
            if self._recovered:
                return self._recovery_result()
            connection: sqlite3.Connection | None = None
            pending_stream_failures: list[
                tuple[
                    bytes,
                    ContextAdmissionStreamKey,
                    ContextAdmissionStorageFailureReason,
                    str,
                ]
            ] = []
            try:
                self._ensure_store()
                connection = self._connect()
                connection.execute("BEGIN")
                connection.setlimit(
                    sqlite3.SQLITE_LIMIT_LENGTH,
                    max(1, _MAX_RECOVERY_BYTES),
                )
                read_budget = _LedgerReadBudget(
                    "recovery-read-limit-exceeded",
                    max_rows=_MAX_RECOVERY_ROWS,
                    max_bytes=_MAX_RECOVERY_BYTES,
                )
                self._validate_integrity(connection)
                metadata = dict(
                    _read_bounded_rows(
                        connection.execute("SELECT key, value FROM metadata"),
                        read_budget,
                    )
                )
                self._validate_metadata(metadata)
                _preflight_storage_routes(connection, read_budget)
                self._stream_health.clear()
                self._unresolved_streams.clear()
                stream_rows = _read_bounded_rows(
                    connection.execute(
                        """
                        SELECT stream_id, stream_key, genesis_envelope, state_envelope,
                               aggregate_revision, admission_sequence,
                               latest_journal_sequence, health_status,
                               failure_reason, reason_code
                        FROM streams
                        ORDER BY stream_id
                        """
                    ),
                    read_budget,
                )
                for row in stream_rows:
                    stream_id = bytes(row[0])
                    stream_key = _decode_stream_key(bytes(row[1]))
                    if stream_id != bytes(row[1]) or stream_id != _stream_key_bytes(stream_key):
                        raise _LedgerOpenError(
                            ContextAdmissionStorageFailureReason.IDENTITY_MISMATCH,
                            "stream-key-mismatch",
                        )
                    try:
                        health = _stored_stream_health(stream_key, row[7], row[8], row[9])
                    except (ContextAdmissionValidationError, ValueError) as exc:
                        raise _LedgerOpenError(
                            ContextAdmissionStorageFailureReason.INTEGRITY,
                            "invalid-stream-health",
                        ) from exc
                    if health.status is ContextAdmissionStorageHealthStatus.FAIL_CLOSED:
                        self._stream_health[stream_key] = health
                        continue
                    try:
                        recovered_state = _recover_stream_projection(
                            connection,
                            stream_id,
                            stream_key,
                            genesis_envelope=bytes(row[2]),
                            materialized_state_envelope=bytes(row[3]),
                            aggregate_revision=int(row[4]),
                            admission_sequence=int(row[5]),
                            latest_journal_sequence=int(row[6]),
                            read_budget=read_budget,
                        )
                    except ContextAdmissionValidationError:
                        pending_stream_failures.append(
                            (
                                stream_id,
                                stream_key,
                                ContextAdmissionStorageFailureReason.REPLAY_MISMATCH,
                                "stream-replay-decode-failed",
                            )
                        )
                        continue
                    except _LedgerOpenError as exc:
                        pending_stream_failures.append(
                            (
                                stream_id,
                                stream_key,
                                exc.reason,
                                exc.reason_code,
                            )
                        )
                        continue
                    self._stream_health[stream_key] = ContextAdmissionStreamHealth(
                        stream_key,
                        ContextAdmissionStorageHealthStatus.HEALTHY,
                    )
                    if _state_has_unresolved_work(recovered_state):
                        self._unresolved_streams.add(stream_key)
                connection.execute("COMMIT")
                for stream_id, stream_key, reason, reason_code in pending_stream_failures:
                    persisted = self._persist_stream_failure(
                        connection,
                        stream_id,
                        stream_key,
                        reason,
                        reason_code,
                    )
                    if not persisted:
                        if (
                            self._store_health.status
                            is not ContextAdmissionStorageHealthStatus.FAIL_CLOSED
                        ):
                            raise _LedgerContended
                        break
                if (
                    self._store_health.status
                    is not ContextAdmissionStorageHealthStatus.FAIL_CLOSED
                ):
                    self._store_health = ContextAdmissionStoreHealth(
                        ContextAdmissionStorageHealthStatus.HEALTHY
                    )
                self._recovered = True
            except _LedgerContended:
                self._stream_health.clear()
                self._unresolved_streams.clear()
                return ContextAdmissionRecoveryResult(
                    status=ContextAdmissionStorageHealthStatus.UNINITIALIZED,
                    store_health=self._store_health,
                    stream_healths=(),
                    recovered_streams=(),
                    unresolved_streams=(),
                )
            except _LedgerOpenError as exc:
                self._set_store_failure(exc.reason, exc.reason_code)
            except sqlite3.Error as exc:
                primary_code = _sqlite_primary_code(exc)
                if primary_code in _SQLITE_BUSY_CODES:
                    if connection is not None:
                        _rollback(connection)
                    self._stream_health.clear()
                    self._unresolved_streams.clear()
                    return ContextAdmissionRecoveryResult(
                        status=ContextAdmissionStorageHealthStatus.UNINITIALIZED,
                        store_health=self._store_health,
                        stream_healths=(),
                        recovered_streams=(),
                        unresolved_streams=(),
                    )
                if primary_code == sqlite3.SQLITE_TOOBIG:
                    self._set_store_failure(
                        ContextAdmissionStorageFailureReason.INTEGRITY,
                        "recovery-read-limit-exceeded",
                    )
                    return self._recovery_result()
                reason = (
                    ContextAdmissionStorageFailureReason.INTEGRITY
                    if primary_code == sqlite3.SQLITE_CORRUPT
                    else ContextAdmissionStorageFailureReason.IO
                )
                self._set_store_failure(reason, "sqlite-recovery-failed")
            finally:
                if connection is not None:
                    connection.close()
            return self._recovery_result()

    def replay(
        self,
        stream_key: ContextAdmissionStreamKey,
    ) -> ContextAdmissionInspectionResult:
        return self.inspect_stream(stream_key)

    def inspect_stream(
        self,
        stream_key: ContextAdmissionStreamKey,
    ) -> ContextAdmissionInspectionResult:
        with self._fence:
            if not self._recovered:
                self.recover_all()
            if not self._recovered:
                return _contended_inspection(stream_key)
            if self._store_health.status is ContextAdmissionStorageHealthStatus.FAIL_CLOSED:
                health = ContextAdmissionStreamHealth(
                    stream_key,
                    ContextAdmissionStorageHealthStatus.FAIL_CLOSED,
                    failure_reason=self._store_health.failure_reason,
                    reason_code=self._store_health.reason_code,
                )
                return _empty_inspection(stream_key, health)
            connection: sqlite3.Connection | None = None
            stream_id = _stream_key_bytes(stream_key)
            try:
                connection = self._connect()
                connection.execute("BEGIN")
                connection.setlimit(
                    sqlite3.SQLITE_LIMIT_LENGTH,
                    max(1, _MAX_RECOVERY_BYTES),
                )
                read_budget = _LedgerReadBudget(
                    "inspection-read-limit-exceeded",
                    max_rows=_MAX_RECOVERY_ROWS,
                    max_bytes=_MAX_RECOVERY_BYTES,
                )
                row = connection.execute(
                    """
                    SELECT stream_key, state_envelope, latest_journal_sequence,
                           health_status, failure_reason, reason_code
                    FROM streams WHERE stream_id = ?
                    """,
                    (stream_id,),
                ).fetchone()
                if row is None:
                    return _empty_inspection(
                        stream_key,
                        ContextAdmissionStreamHealth(
                            stream_key,
                            ContextAdmissionStorageHealthStatus.UNINITIALIZED,
                        ),
                    )
                row = read_budget.consume(cast(tuple[Any, ...], row))
                if bytes(row[0]) != stream_id:
                    raise _LedgerOpenError(
                        ContextAdmissionStorageFailureReason.IDENTITY_MISMATCH,
                        "stream-key-mismatch",
                    )
                health = ContextAdmissionStreamHealth(
                    stream_key,
                    ContextAdmissionStorageHealthStatus(row[3]),
                    failure_reason=(
                        ContextAdmissionStorageFailureReason(row[4])
                        if row[4] is not None
                        else None
                    ),
                    reason_code=str(row[5]) if row[5] is not None else None,
                )
                if health.status is ContextAdmissionStorageHealthStatus.FAIL_CLOSED:
                    self._stream_health[stream_key] = health
                    return _empty_inspection(stream_key, health)
                latest = int(row[2])
                journal_rows = _read_bounded_rows(
                    connection.execute(
                        """
                        SELECT journal_sequence, event_envelope, decision_envelope
                        FROM journal_events
                        WHERE stream_id = ?
                        ORDER BY journal_sequence
                        """,
                        (stream_id,),
                    ),
                    read_budget,
                )
                effect_rows = _read_bounded_rows(
                    connection.execute(
                        """
                        SELECT journal_sequence, effect_ordinal, effect_envelope
                        FROM effect_outbox
                        WHERE stream_id = ?
                        ORDER BY journal_sequence, effect_ordinal
                        """,
                        (stream_id,),
                    ),
                    read_budget,
                )
                shadow_rows = _read_bounded_rows(
                    connection.execute(
                        """
                        SELECT journal_sequence, shadow_envelope
                        FROM shadow_decisions
                        WHERE stream_id = ?
                        ORDER BY journal_sequence
                        """,
                        (stream_id,),
                    ),
                    read_budget,
                )
                sequences = tuple(int(item[0]) for item in journal_rows)
                if latest != len(sequences) or any(
                    sequence != expected for expected, sequence in enumerate(sequences, start=1)
                ):
                    raise _LedgerOpenError(
                        ContextAdmissionStorageFailureReason.REPLAY_MISMATCH,
                        "journal-sequence-gap",
                    )
                effects_by_sequence: dict[int, list[AdmissionEffect]] = {
                    sequence: [] for sequence in sequences
                }
                for sequence, ordinal, envelope in effect_rows:
                    effects = effects_by_sequence.get(int(sequence))
                    if effects is None or int(ordinal) != len(effects):
                        raise _LedgerOpenError(
                            ContextAdmissionStorageFailureReason.REPLAY_MISMATCH,
                            "effect-sequence-gap",
                        )
                    effects.append(_decode_effect(bytes(envelope)))
                shadows = {
                    int(sequence): _decode_shadow(bytes(envelope))
                    for sequence, envelope in shadow_rows
                }
                if tuple(shadows) != sequences:
                    raise _LedgerOpenError(
                        ContextAdmissionStorageFailureReason.REPLAY_MISMATCH,
                        "shadow-sequence-gap",
                    )
                inspection = ContextAdmissionInspectionResult(
                    stream_key=stream_key,
                    health=health,
                    state=_decode_state(bytes(row[1])),
                    events=tuple(_decode_event(bytes(item[1])) for item in journal_rows),
                    decisions=tuple(_decode_decision(bytes(item[2])) for item in journal_rows),
                    effects=tuple(tuple(effects_by_sequence[sequence]) for sequence in sequences),
                    shadows=tuple(shadows[sequence] for sequence in sequences),
                    latest_journal_sequence=latest,
                )
                self._stream_health[stream_key] = health
                return inspection
            except _LedgerContended:
                return _contended_inspection(stream_key)
            except sqlite3.Error as exc:
                primary_code = _sqlite_primary_code(exc)
                if connection is not None:
                    _rollback(connection)
                if primary_code in _SQLITE_BUSY_CODES or primary_code in _SQLITE_RECOVERY_CODES:
                    return _contended_inspection(stream_key)
                if primary_code == sqlite3.SQLITE_TOOBIG:
                    self._set_store_failure(
                        ContextAdmissionStorageFailureReason.INTEGRITY,
                        "inspection-read-limit-exceeded",
                    )
                    return _empty_inspection(
                        stream_key,
                        ContextAdmissionStreamHealth(
                            stream_key,
                            ContextAdmissionStorageHealthStatus.FAIL_CLOSED,
                            failure_reason=self._store_health.failure_reason,
                            reason_code=self._store_health.reason_code,
                        ),
                    )
                reason = (
                    ContextAdmissionStorageFailureReason.INTEGRITY
                    if primary_code in {sqlite3.SQLITE_CORRUPT, sqlite3.SQLITE_CONSTRAINT}
                    else ContextAdmissionStorageFailureReason.IO
                )
                self._set_store_failure(reason, "sqlite-inspection-failed")
                return _empty_inspection(
                    stream_key,
                    ContextAdmissionStreamHealth(
                        stream_key,
                        ContextAdmissionStorageHealthStatus.FAIL_CLOSED,
                        failure_reason=self._store_health.failure_reason,
                        reason_code=self._store_health.reason_code,
                    ),
                )
            except (ContextAdmissionValidationError, _LedgerOpenError) as exc:
                reason = (
                    exc.reason
                    if isinstance(exc, _LedgerOpenError)
                    else ContextAdmissionStorageFailureReason.REPLAY_MISMATCH
                )
                reason_code = (
                    exc.reason_code
                    if isinstance(exc, _LedgerOpenError)
                    else "inspection-decode-failed"
                )
                if connection is not None:
                    _rollback(connection)
                persisted = connection is not None and self._persist_stream_failure(
                    connection,
                    stream_id,
                    stream_key,
                    reason,
                    reason_code,
                )
                if not persisted:
                    if (
                        self._store_health.status
                        is ContextAdmissionStorageHealthStatus.FAIL_CLOSED
                    ):
                        return _empty_inspection(
                            stream_key,
                            ContextAdmissionStreamHealth(
                                stream_key,
                                ContextAdmissionStorageHealthStatus.FAIL_CLOSED,
                                failure_reason=self._store_health.failure_reason,
                                reason_code=self._store_health.reason_code,
                            ),
                        )
                    return _contended_inspection(stream_key)
                return _empty_inspection(
                    stream_key,
                    self.stream_health(stream_key),
                )
            finally:
                if connection is not None:
                    connection.close()

    def _recovery_result(self) -> ContextAdmissionRecoveryResult:
        healths = tuple(
            sorted(
                self._stream_health.values(),
                key=lambda item: _stream_key_bytes(item.stream_key),
            )
        )
        return ContextAdmissionRecoveryResult(
            status=self._store_health.status,
            store_health=self._store_health,
            stream_healths=healths,
            recovered_streams=tuple(
                health.stream_key
                for health in healths
                if health.status is ContextAdmissionStorageHealthStatus.HEALTHY
            ),
            unresolved_streams=tuple(sorted(self._unresolved_streams, key=_stream_key_bytes)),
        )

    def _ensure_store(self) -> None:
        self._ensure_private_parent()
        if self._path.exists():
            self._recover_initialization_link()
            self._validate_database_file()
            self._validate_sidecars(allow_regular=True)
            return
        self._validate_sidecars(allow_regular=False)
        temporary_path = self._path.parent / (f".{self._path.name}.{secrets.token_hex(12)}.tmp")
        try:
            descriptor = os.open(
                temporary_path,
                os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                _DATABASE_MODE,
            )
            os.close(descriptor)
            connection = self._configure_connection(temporary_path)
            try:
                connection.execute("BEGIN IMMEDIATE")
                for statement in _SCHEMA_SQL.split(";"):
                    if statement.strip():
                        connection.execute(statement)
                metadata = {
                    "schema_version": str(_SCHEMA_VERSION),
                    "encoding_version": str(CONTEXT_ADMISSION_ENCODING_VERSION),
                    "protocol_version": str(CONTEXT_ADMISSION_PROTOCOL_VERSION),
                    "store_health": ContextAdmissionStorageHealthStatus.HEALTHY.value,
                }
                connection.executemany(
                    "INSERT INTO metadata(key, value) VALUES(?, ?)",
                    tuple(metadata.items()),
                )
                connection.execute("COMMIT")
            except BaseException:
                _rollback(connection)
                raise
            finally:
                connection.close()
            os.chmod(temporary_path, _DATABASE_MODE)
            _fsync_file(temporary_path)
            os.link(temporary_path, self._path, follow_symlinks=False)
            _unlink_initialization_artifact(temporary_path)
            _fsync_directory(self._path.parent)
            self._validate_database_file()
        except FileExistsError as exc:
            if self._path.exists():
                self._recover_initialization_link()
                self._validate_database_file()
                return
            raise _LedgerOpenError(
                ContextAdmissionStorageFailureReason.SECURITY_IDENTITY,
                "store-publication-collision",
            ) from exc
        except _LedgerOpenError:
            raise
        except (OSError, sqlite3.Error) as exc:
            raise _LedgerOpenError(
                ContextAdmissionStorageFailureReason.IO,
                "store-initialization-failed",
            ) from exc
        finally:
            _unlink_initialization_artifact(temporary_path)

    def _recover_initialization_link(self) -> None:
        deadline = time.monotonic() + (self._busy_timeout_ms / 1_000)
        try:
            while self._has_initialization_link():
                if time.monotonic() < deadline:
                    time.sleep(min(0.005, max(0.0, deadline - time.monotonic())))
                    continue
                if reconcile_initialization_links(
                    self._path,
                    owner_id=self._authority.expected_owner_id,
                    file_mode=_DATABASE_MODE,
                    remove=True,
                ):
                    _fsync_directory(self._path.parent)
                return
        except (FileNotFoundError, NotADirectoryError):
            return
        except OSError as exc:
            raise _LedgerOpenError(
                ContextAdmissionStorageFailureReason.IO,
                "store-initialization-link-recovery-failed",
            ) from exc

    def _has_initialization_link(self) -> bool:
        return reconcile_initialization_links(
            self._path,
            owner_id=self._authority.expected_owner_id,
            file_mode=_DATABASE_MODE,
            remove=False,
        )

    def _ensure_private_parent(self) -> None:
        parent = self._path.parent
        trusted_parent = parent.parent
        try:
            trusted_stat = trusted_parent.lstat()
            if (
                not stat.S_ISDIR(trusted_stat.st_mode)
                or trusted_stat.st_uid != self._authority.expected_owner_id
                or trusted_stat.st_mode & (stat.S_IWGRP | stat.S_IWOTH)
            ):
                raise _LedgerOpenError(
                    ContextAdmissionStorageFailureReason.SECURITY_IDENTITY,
                    "untrusted-store-parent",
                )
            try:
                parent.mkdir(mode=_DIRECTORY_MODE)
                _fsync_directory(trusted_parent)
            except FileExistsError:
                pass
            parent_stat = parent.lstat()
        except _LedgerOpenError:
            raise
        except OSError as exc:
            raise _LedgerOpenError(
                ContextAdmissionStorageFailureReason.IO,
                "store-parent-unavailable",
            ) from exc
        if (
            not stat.S_ISDIR(parent_stat.st_mode)
            or parent_stat.st_uid != self._authority.expected_owner_id
            or stat.S_IMODE(parent_stat.st_mode) != _DIRECTORY_MODE
        ):
            raise _LedgerOpenError(
                ContextAdmissionStorageFailureReason.SECURITY_IDENTITY,
                "insecure-store-parent",
            )

    def _validate_database_file(self) -> tuple[int, int]:
        return _private_file_identity(
            self._path,
            owner_id=self._authority.expected_owner_id,
            reason_code="insecure-store-file",
        )

    def _validate_sidecars(self, *, allow_regular: bool) -> None:
        for suffix in ("-journal", "-wal", "-shm"):
            sidecar = Path(f"{self._path}{suffix}")
            try:
                sidecar.lstat()
            except FileNotFoundError:
                continue
            if not allow_regular:
                raise _LedgerOpenError(
                    ContextAdmissionStorageFailureReason.SECURITY_IDENTITY,
                    "orphan-store-sidecar",
                )
            _private_file_identity(
                sidecar,
                owner_id=self._authority.expected_owner_id,
                reason_code="insecure-store-sidecar",
            )

    def _connect(self) -> sqlite3.Connection:
        before = self._validate_database_file()
        self._validate_sidecars(allow_regular=True)
        connection = self._configure_connection(self._path)
        try:
            after = self._validate_database_file()
            if before != after:
                raise _LedgerOpenError(
                    ContextAdmissionStorageFailureReason.SECURITY_IDENTITY,
                    "store-identity-changed",
                )
            return connection
        except BaseException:
            connection.close()
            raise

    def _configure_connection(self, path: Path) -> sqlite3.Connection:
        try:
            connection = self._connection_factory(
                f"{path.as_uri()}?mode=rw",
                uri=True,
                timeout=self._busy_timeout_ms / 1_000,
                isolation_level=None,
            )
            expected_pragmas = (
                ("journal_mode", "DELETE", ("delete",)),
                ("synchronous", "EXTRA", (3,)),
                ("foreign_keys", "ON", (1,)),
                ("busy_timeout", str(self._busy_timeout_ms), (self._busy_timeout_ms,)),
            )
            for name, value, expected in expected_pragmas:
                connection.execute(f"PRAGMA {name}={value}")
                row = connection.execute(f"PRAGMA {name}").fetchone()
                if row != expected:
                    raise _LedgerOpenError(
                        ContextAdmissionStorageFailureReason.CONFIGURATION,
                        "sqlite-pragma-mismatch",
                    )
            return connection
        except _LedgerOpenError:
            raise
        except sqlite3.Error as exc:
            if _sqlite_primary_code(exc) in _SQLITE_BUSY_CODES:
                raise _LedgerContended from exc
            raise

    @staticmethod
    def _validate_integrity(connection: sqlite3.Connection) -> None:
        row = connection.execute("PRAGMA integrity_check").fetchone()
        if row != ("ok",):
            raise _LedgerOpenError(
                ContextAdmissionStorageFailureReason.INTEGRITY,
                "sqlite-integrity-failed",
            )
        if connection.execute("PRAGMA foreign_key_check").fetchone() is not None:
            raise _LedgerOpenError(
                ContextAdmissionStorageFailureReason.INTEGRITY,
                "sqlite-foreign-key-check-failed",
            )

    @staticmethod
    def _validate_metadata(metadata: dict[str, str]) -> None:
        expected = {
            "schema_version": str(_SCHEMA_VERSION),
            "encoding_version": str(CONTEXT_ADMISSION_ENCODING_VERSION),
            "protocol_version": str(CONTEXT_ADMISSION_PROTOCOL_VERSION),
            "store_health": ContextAdmissionStorageHealthStatus.HEALTHY.value,
        }
        for key, value in expected.items():
            if metadata.get(key) != value:
                reason = {
                    "schema_version": ContextAdmissionStorageFailureReason.UNSUPPORTED_SCHEMA,
                    "encoding_version": ContextAdmissionStorageFailureReason.UNSUPPORTED_ENCODING,
                    "protocol_version": ContextAdmissionStorageFailureReason.UNSUPPORTED_PROTOCOL,
                }.get(key, ContextAdmissionStorageFailureReason.INTEGRITY)
                raise _LedgerOpenError(reason, f"invalid-{key.replace('_', '-')}")

    def _set_store_failure(
        self,
        reason: ContextAdmissionStorageFailureReason,
        reason_code: str,
    ) -> None:
        self._store_health = ContextAdmissionStoreHealth(
            ContextAdmissionStorageHealthStatus.FAIL_CLOSED,
            failure_reason=reason,
            reason_code=reason_code,
        )
        self._recovered = True


def _zero_state(protocol_version: int) -> UninitializedContextAdmissionState:
    context_admission_reducer_for_protocol(protocol_version)
    return UninitializedContextAdmissionState(
        protocol_version=protocol_version,
        aggregate_revision=AggregateRevision(0),
        admission_sequence=AdmissionSequence(0),
        processed_events=(),
        idempotency_records=(),
        expired_idempotency_tombstones=(),
        closed_epochs=(),
    )


def _encode_value(
    value: DurableContextAdmissionPayload,
    *,
    protocol_version: int,
) -> bytes:
    return encode_stored_context_admission_envelope(
        make_stored_context_admission_envelope(
            value,
            protocol_version=protocol_version,
        )
    )


def _decode_event(value: bytes) -> ContextAdmissionEvent:
    payload = decode_stored_context_admission_envelope(value).payload
    if not isinstance(payload, _EVENT_TYPES):
        raise ContextAdmissionValidationError("stored_event_type_mismatch")
    return cast(ContextAdmissionEvent, payload)


def _decode_decision(value: bytes) -> AdmissionDecision:
    payload = decode_stored_context_admission_envelope(value).payload
    if not isinstance(payload, AdmissionDecision):
        raise ContextAdmissionValidationError("stored_decision_type_mismatch")
    return payload


def _decode_effect(value: bytes) -> AdmissionEffect:
    payload = decode_stored_context_admission_envelope(value).payload
    if not isinstance(payload, _EFFECT_TYPES):
        raise ContextAdmissionValidationError("stored_effect_type_mismatch")
    return cast(AdmissionEffect, payload)


def _decode_shadow(value: bytes) -> ShadowContextAdmissionRecord:
    payload = decode_stored_context_admission_envelope(value).payload
    if not isinstance(payload, ShadowContextAdmissionRecord):
        raise ContextAdmissionValidationError("stored_shadow_type_mismatch")
    return payload


def _decode_state(value: bytes) -> ContextAdmissionState:
    payload = decode_stored_context_admission_envelope(value).payload
    if not isinstance(payload, _STATE_TYPES):
        raise ContextAdmissionValidationError("stored_state_type_mismatch")
    return cast(ContextAdmissionState, payload)


def _decode_stream_key(value: bytes) -> ContextAdmissionStreamKey:
    _validate_stream_key_json_bounds(value)
    try:
        raw = json.loads(value.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError):
        raise _LedgerOpenError(
            ContextAdmissionStorageFailureReason.IDENTITY_MISMATCH,
            "invalid-stream-key",
        ) from None
    if not isinstance(raw, dict):
        raise _LedgerOpenError(
            ContextAdmissionStorageFailureReason.IDENTITY_MISMATCH,
            "invalid-stream-key",
        )
    try:
        stream_key = ContextAdmissionStreamKey.from_dict(raw)
    except ContextAdmissionValidationError as exc:
        raise _LedgerOpenError(
            ContextAdmissionStorageFailureReason.IDENTITY_MISMATCH,
            "invalid-stream-key",
        ) from exc
    if _stream_key_bytes(stream_key) != value:
        raise _LedgerOpenError(
            ContextAdmissionStorageFailureReason.IDENTITY_MISMATCH,
            "noncanonical-stream-key",
        )
    return stream_key


def _validate_stream_key_json_bounds(value: bytes) -> None:
    if not value or len(value) > _MAX_STREAM_KEY_BYTES:
        raise _LedgerOpenError(
            ContextAdmissionStorageFailureReason.IDENTITY_MISMATCH,
            "invalid-stream-key",
        )
    depth = 0
    in_string = False
    escaped = False
    for character in value:
        if in_string:
            if escaped:
                escaped = False
            elif character == ord("\\"):
                escaped = True
            elif character == ord('"'):
                in_string = False
            continue
        if character == ord('"'):
            in_string = True
        elif character in {ord("{"), ord("[")}:
            depth += 1
            if depth > _MAX_STREAM_KEY_JSON_NESTING:
                raise _LedgerOpenError(
                    ContextAdmissionStorageFailureReason.IDENTITY_MISMATCH,
                    "invalid-stream-key",
                )
        elif character in {ord("}"), ord("]")}:
            depth -= 1
            if depth < 0:
                raise _LedgerOpenError(
                    ContextAdmissionStorageFailureReason.IDENTITY_MISMATCH,
                    "invalid-stream-key",
                )


def _stored_stream_health(
    stream_key: ContextAdmissionStreamKey,
    status: object,
    failure_reason: object,
    reason_code: object,
) -> ContextAdmissionStreamHealth:
    return ContextAdmissionStreamHealth(
        stream_key,
        ContextAdmissionStorageHealthStatus(str(status)),
        failure_reason=(
            ContextAdmissionStorageFailureReason(str(failure_reason))
            if failure_reason is not None
            else None
        ),
        reason_code=str(reason_code) if reason_code is not None else None,
    )


def _recover_stream_projection(
    connection: sqlite3.Connection,
    stream_id: bytes,
    stream_key: ContextAdmissionStreamKey,
    *,
    genesis_envelope: bytes,
    materialized_state_envelope: bytes,
    aggregate_revision: int,
    admission_sequence: int,
    latest_journal_sequence: int,
    read_budget: _LedgerReadBudget,
) -> ContextAdmissionState:
    genesis_wrapper = decode_stored_context_admission_envelope(genesis_envelope)
    if not isinstance(genesis_wrapper.payload, UninitializedContextAdmissionState):
        raise _LedgerOpenError(
            ContextAdmissionStorageFailureReason.REPLAY_MISMATCH,
            "invalid-stream-genesis-type",
        )
    genesis = genesis_wrapper.payload
    if genesis_wrapper.protocol_version != genesis.protocol_version or genesis != _zero_state(
        genesis.protocol_version
    ):
        raise _LedgerOpenError(
            ContextAdmissionStorageFailureReason.REPLAY_MISMATCH,
            "invalid-stream-genesis",
        )
    if latest_journal_sequence <= 0:
        raise _LedgerOpenError(
            ContextAdmissionStorageFailureReason.AMBIGUOUS_RECOVERY,
            "empty-bound-stream",
        )
    journal_rows = _read_bounded_rows(
        connection.execute(
            """
            SELECT journal_sequence, event_id, event_envelope, decision_envelope,
                   expected_revision, prior_aggregate_revision,
                   prior_admission_sequence, resulting_aggregate_revision,
                   resulting_admission_sequence
            FROM journal_events
            WHERE stream_id = ?
            ORDER BY journal_sequence
            """,
            (stream_id,),
        ),
        read_budget,
    )
    sequences = tuple(int(row[0]) for row in journal_rows)
    if latest_journal_sequence != len(sequences) or any(
        sequence != expected for expected, sequence in enumerate(sequences, start=1)
    ):
        raise _LedgerOpenError(
            ContextAdmissionStorageFailureReason.REPLAY_MISMATCH,
            "journal-sequence-gap",
        )
    effects_by_sequence: dict[int, list[bytes]] = {sequence: [] for sequence in sequences}
    effect_rows = _read_bounded_rows(
        connection.execute(
            """
            SELECT journal_sequence, effect_ordinal, effect_envelope
            FROM effect_outbox
            WHERE stream_id = ?
            ORDER BY journal_sequence, effect_ordinal
            """,
            (stream_id,),
        ),
        read_budget,
    )
    for sequence, ordinal, envelope in effect_rows:
        effects = effects_by_sequence.get(int(sequence))
        if effects is None or int(ordinal) != len(effects):
            raise _LedgerOpenError(
                ContextAdmissionStorageFailureReason.REPLAY_MISMATCH,
                "effect-sequence-gap",
            )
        effects.append(bytes(envelope))
    shadow_rows = _read_bounded_rows(
        connection.execute(
            """
            SELECT journal_sequence, shadow_envelope
            FROM shadow_decisions
            WHERE stream_id = ?
            ORDER BY journal_sequence
            """,
            (stream_id,),
        ),
        read_budget,
    )
    if tuple(int(row[0]) for row in shadow_rows) != sequences:
        raise _LedgerOpenError(
            ContextAdmissionStorageFailureReason.REPLAY_MISMATCH,
            "shadow-sequence-gap",
        )
    shadow_by_sequence = {int(sequence): bytes(envelope) for sequence, envelope in shadow_rows}
    state: ContextAdmissionState = genesis
    for row in journal_rows:
        journal_sequence = int(row[0])
        event_wrapper = decode_stored_context_admission_envelope(bytes(row[2]))
        decision_wrapper = decode_stored_context_admission_envelope(bytes(row[3]))
        if not isinstance(event_wrapper.payload, _EVENT_TYPES) or not isinstance(
            decision_wrapper.payload,
            AdmissionDecision,
        ):
            raise ContextAdmissionValidationError("stored_publication_type_mismatch")
        event = cast(ContextAdmissionEvent, event_wrapper.payload)
        stored_decision = decision_wrapper.payload
        if str(row[1]) != event.event_id.value:
            raise _LedgerOpenError(
                ContextAdmissionStorageFailureReason.REPLAY_MISMATCH,
                "journal-event-identity-mismatch",
            )
        if journal_sequence == 1 and not isinstance(
            event,
            OpenEpochEvent | AuthorityUnavailableEvent,
        ):
            raise _LedgerOpenError(
                ContextAdmissionStorageFailureReason.REPLAY_MISMATCH,
                "invalid-initial-event",
            )
        protocol_version = event.protocol_version
        if (
            event_wrapper.protocol_version != protocol_version
            or decision_wrapper.protocol_version != protocol_version
            or state.protocol_version != protocol_version
        ):
            raise _LedgerOpenError(
                ContextAdmissionStorageFailureReason.REPLAY_MISMATCH,
                "publication-protocol-mismatch",
            )
        _validate_event_stream_identity(stream_key, event)
        if (
            int(row[4]) != event.expected_aggregate_revision.value
            or int(row[5]) != state.aggregate_revision.value
            or int(row[6]) != state.admission_sequence.value
        ):
            raise _LedgerOpenError(
                ContextAdmissionStorageFailureReason.REPLAY_MISMATCH,
                "journal-prior-coordinate-mismatch",
            )
        reducer = context_admission_reducer_for_protocol(protocol_version)
        transition = reducer.reduce_transition(state, event)
        if stored_decision != transition.decision:
            raise _LedgerOpenError(
                ContextAdmissionStorageFailureReason.REPLAY_MISMATCH,
                "journal-decision-mismatch",
            )
        stored_effects: list[AdmissionEffect] = []
        for encoded_effect in effects_by_sequence[journal_sequence]:
            effect_wrapper = decode_stored_context_admission_envelope(encoded_effect)
            if effect_wrapper.protocol_version != protocol_version or not isinstance(
                effect_wrapper.payload, _EFFECT_TYPES
            ):
                raise _LedgerOpenError(
                    ContextAdmissionStorageFailureReason.REPLAY_MISMATCH,
                    "effect-protocol-mismatch",
                )
            stored_effects.append(cast(AdmissionEffect, effect_wrapper.payload))
        if tuple(stored_effects) != transition.effects:
            raise _LedgerOpenError(
                ContextAdmissionStorageFailureReason.REPLAY_MISMATCH,
                "journal-effects-mismatch",
            )
        if (
            int(row[7]) != transition.next_state.aggregate_revision.value
            or int(row[8]) != transition.next_state.admission_sequence.value
        ):
            raise _LedgerOpenError(
                ContextAdmissionStorageFailureReason.REPLAY_MISMATCH,
                "journal-result-coordinate-mismatch",
            )
        shadow_wrapper = decode_stored_context_admission_envelope(
            shadow_by_sequence[journal_sequence]
        )
        regenerated_shadow = _shadow_record(
            stream_key,
            state,
            event,
            transition,
            journal_sequence,
        )
        if (
            shadow_wrapper.protocol_version != protocol_version
            or not isinstance(
                shadow_wrapper.payload,
                ShadowContextAdmissionRecord,
            )
            or shadow_wrapper.payload != regenerated_shadow
        ):
            raise _LedgerOpenError(
                ContextAdmissionStorageFailureReason.REPLAY_MISMATCH,
                "journal-shadow-mismatch",
            )
        state = transition.next_state
    materialized_wrapper = decode_stored_context_admission_envelope(materialized_state_envelope)
    if (
        materialized_wrapper.protocol_version != state.protocol_version
        or not isinstance(materialized_wrapper.payload, _STATE_TYPES)
        or materialized_wrapper.payload != state
        or aggregate_revision != state.aggregate_revision.value
        or admission_sequence != state.admission_sequence.value
    ):
        raise _LedgerOpenError(
            ContextAdmissionStorageFailureReason.REPLAY_MISMATCH,
            "materialized-state-mismatch",
        )
    return state


def _state_has_unresolved_work(state: ContextAdmissionState) -> bool:
    if not isinstance(state, ActiveContextAdmissionState):
        return False
    unresolved_admission_states = {
        AdmissionState.RESERVED,
        AdmissionState.PREPARED,
        AdmissionState.HISTORY_STAGED,
        AdmissionState.REQUEST_DISPATCHED,
        AdmissionState.INDETERMINATE,
        AdmissionState.QUARANTINED,
    }
    unresolved_generation_states = {
        GenerationState.RESERVED,
        GenerationState.STREAMING,
        GenerationState.INDETERMINATE,
        GenerationState.QUARANTINED,
    }
    return any(
        record.state in unresolved_admission_states for record in state.batch_records
    ) or any(
        record.state in unresolved_generation_states for record in state.generation_reservations
    )


def _empty_inspection(
    stream_key: ContextAdmissionStreamKey,
    health: ContextAdmissionStreamHealth,
) -> ContextAdmissionInspectionResult:
    return ContextAdmissionInspectionResult(
        stream_key=stream_key,
        health=health,
        state=None,
        events=(),
        decisions=(),
        effects=(),
        shadows=(),
        latest_journal_sequence=0,
    )


def _contended_inspection(
    stream_key: ContextAdmissionStreamKey,
) -> ContextAdmissionInspectionResult:
    return _empty_inspection(
        stream_key,
        ContextAdmissionStreamHealth(
            stream_key,
            ContextAdmissionStorageHealthStatus.UNINITIALIZED,
        ),
    )


def _state_retains_event(state: ContextAdmissionState, event_id: str) -> bool:
    return any(record.event_id.value == event_id for record in state.processed_events)


def _validate_event_stream_identity(
    stream_key: ContextAdmissionStreamKey,
    event: ContextAdmissionEvent,
) -> None:
    for lineage in _iter_lineages(event):
        if (
            lineage.root_session_id != stream_key.root_session_id
            or lineage.current_session_id != stream_key.current_session_id
            or lineage.root_agent_id != stream_key.root_agent_id
            or lineage.current_agent_id != stream_key.current_agent_id
            or lineage.root_thread_id != stream_key.root_thread_id
            or lineage.current_thread_id != stream_key.current_thread_id
            or lineage.fork_occurrence_id != stream_key.fork_occurrence_id
        ):
            raise _LedgerOpenError(
                ContextAdmissionStorageFailureReason.IDENTITY_MISMATCH,
                "stream-identity-mismatch",
            )


def _iter_lineages(value: object) -> tuple[ContextLineage, ...]:
    lineages: list[ContextLineage] = []

    def visit(item: object) -> None:
        if isinstance(item, ContextLineage):
            lineages.append(item)
            return
        if isinstance(item, tuple | frozenset):
            for child in item:
                visit(child)
            return
        if is_dataclass(item):
            for field_def in fields(item):
                visit(getattr(item, field_def.name))

    visit(value)
    return tuple(lineages)


def _uninitialized_stream_result(
    stream_key: ContextAdmissionStreamKey,
    event: ContextAdmissionEvent,
) -> ContextAdmissionAccountingResult:
    decision = AdmissionDecision(
        kind=AdmissionDecisionKind.WOULD_REJECT,
        reason_code="stream-uninitialized",
        window_epoch_id=None,
        snapshot_sequence=None,
        requested_count=0,
        available_ordinary_count=0,
        available_protected_count=0,
    )
    return ContextAdmissionAccountingResult(
        status=ContextAdmissionAccountingStatus.SEMANTIC_REJECTION,
        stream_key=stream_key,
        transition=AdmissionTransition(
            next_state=_zero_state(event.protocol_version),
            decision=decision,
            effects=(),
        ),
        reason_code=decision.reason_code,
    )


def _shadow_record(
    stream_key: ContextAdmissionStreamKey,
    prior_state: ContextAdmissionState,
    event: ContextAdmissionEvent,
    transition: AdmissionTransition,
    journal_sequence: int,
) -> ShadowContextAdmissionRecord:
    targets = _shadow_targets(prior_state, event, transition.next_state)
    return ShadowContextAdmissionRecord(
        stream_key=stream_key,
        event_id=event.event_id,
        journal_sequence=journal_sequence,
        aggregate_revision=transition.next_state.aggregate_revision,
        admission_sequence=transition.next_state.admission_sequence,
        decision=transition.decision,
        protocol_version=event.protocol_version,
        encoding_version=CONTEXT_ADMISSION_ENCODING_VERSION,
        reason_code=transition.decision.reason_code,
        targets=tuple(
            sorted(
                targets,
                key=lambda target: (
                    type(target.target_id).__name__,
                    target.target_id.value,
                ),
            )
        ),
    )


def _shadow_targets(
    prior_state: ContextAdmissionState,
    event: ContextAdmissionEvent,
    next_state: ContextAdmissionState,
) -> tuple[ShadowContextAdmissionTargetRecord, ...]:
    batch_ids: set[AdmissionBatchId] = set()
    generation_ids: set[GenerationReservationId] = set()
    event_batch: AdmissionBatch | None = None
    event_reservation: AdmissionReservation | None = None
    event_generation: GenerationReservationRecord | None = None
    match event:
        case OpenEpochEvent() | AuthorityUnavailableEvent() | ProposeOccurrenceEvent():
            pass
        case ReserveRequestEvent():
            batch_ids.add(event.batch.batch_id)
            event_batch = event.batch
            event_reservation = event.input_reservations[0]
            if event.generation_reservation is not None:
                generation_ids.add(event.generation_reservation.generation_reservation_id)
                event_generation = event.generation_reservation
        case (
            PrepareBatchEvent()
            | StageHistoryEvent()
            | DispatchRequestEvent()
            | AcceptInputEvent()
            | ReleaseNonAdmissionEvent()
            | RollbackAdmissionEvent()
            | MarkIndeterminateEvent()
            | ResolveIndeterminateAcceptedEvent()
            | ResolveIndeterminateNonAdmissionEvent()
            | ResolveIndeterminateRollbackEvent()
        ):
            batch_ids.add(event.batch_id)
        case (
            StartGenerationEvent()
            | ReconcileGenerationEvent()
            | MarkGenerationIndeterminateEvent()
        ):
            generation_ids.add(event.generation_reservation_id)
        case RequestReconciliationEvent():
            if isinstance(event.target_id, AdmissionBatchId):
                batch_ids.add(event.target_id)
            else:
                generation_ids.add(event.target_id)
        case ExpireIdempotencyKeyEvent():
            batch_ids.add(event.reservation_key.batch_id)
        case RolloverEpochEvent():
            if isinstance(prior_state, ActiveContextAdmissionState):
                batch_ids.update(record.batch.batch_id for record in prior_state.batch_records)
                generation_ids.update(
                    record.generation_reservation_id
                    for record in prior_state.generation_reservations
                )
        case _ as unreachable:
            assert_never(unreachable)
    targets: list[ShadowContextAdmissionTargetRecord] = []
    for batch_id in sorted(batch_ids, key=lambda item: item.value):
        target = _input_shadow_target(
            prior_state,
            next_state,
            event,
            batch_id,
            event_batch=event_batch,
            event_reservation=event_reservation,
        )
        if target is not None:
            targets.append(target)
    for generation_id in sorted(generation_ids, key=lambda item: item.value):
        target = _generation_shadow_target(
            prior_state,
            next_state,
            event,
            generation_id,
            event_generation=event_generation,
        )
        if target is not None:
            targets.append(target)
    return tuple(targets)


def _input_shadow_target(
    prior_state: ContextAdmissionState,
    next_state: ContextAdmissionState,
    event: ContextAdmissionEvent,
    batch_id: AdmissionBatchId,
    *,
    event_batch: AdmissionBatch | None,
    event_reservation: AdmissionReservation | None,
) -> ShadowContextAdmissionTargetRecord | None:
    record, reservation = _find_batch(next_state, batch_id)
    if record is None:
        record, reservation = _find_batch(prior_state, batch_id)
    batch_value = record.batch if record is not None else event_batch
    reservation = reservation or event_reservation
    if batch_value is None:
        return None
    lineages = _find_lineages(
        next_state,
        prior_state,
        batch_value.occurrence_ids,
    )
    if lineages is None:
        return None
    lifecycle_state = (
        record.state
        if record is not None
        else _prior_occurrence_state(prior_state, batch_value.occurrence_ids)
    )
    exact_input_charge: int | None = None
    measurement_kind: MeasurementKind | None = None
    if isinstance(event, AcceptInputEvent) and event.batch_id == batch_id:
        exact_input_charge = event.exact_input_charge
        measurement_kind = event.measurement_kind
    elif isinstance(event, ResolveIndeterminateAcceptedEvent) and event.batch_id == batch_id:
        exact_input_charge = event.exact_charge
        measurement_kind = event.measurement_kind
    elif isinstance(event, PrepareBatchEvent) and event.batch_id == batch_id:
        measurement_kind = event.measurement_kind
    return ShadowContextAdmissionTargetRecord(
        target_id=batch_id,
        occurrence_ids=batch_value.occurrence_ids,
        turn_ids=tuple(lineage.turn_id for lineage in lineages),
        tool_call_ids=tuple(lineage.tool_call_id for lineage in lineages),
        producer_instance_ids=tuple(lineage.producer_instance_id for lineage in lineages),
        producer_surfaces=tuple(lineage.producer_surface for lineage in lineages),
        delivery_occurrence_ids=tuple(lineage.delivery_occurrence_id for lineage in lineages),
        reservation_id=(reservation.reservation_id if reservation is not None else None),
        batch_id=batch_id,
        generation_reservation_id=None,
        window_epoch_id=(
            reservation.window_epoch_id if reservation is not None else lineages[0].window_epoch_id
        ),
        reserve_class=batch_value.reserve_class,
        lifecycle_state=lifecycle_state,
        proposed_input_count=(reservation.reserved_count if reservation is not None else None),
        generation_allowance=None,
        exact_input_charge=exact_input_charge,
        exact_output_charge=None,
        measurement_kind=measurement_kind,
    )


def _generation_shadow_target(
    prior_state: ContextAdmissionState,
    next_state: ContextAdmissionState,
    event: ContextAdmissionEvent,
    generation_id: GenerationReservationId,
    *,
    event_generation: GenerationReservationRecord | None,
) -> ShadowContextAdmissionTargetRecord | None:
    record = _find_generation(next_state, generation_id)
    if record is None:
        record = _find_generation(prior_state, generation_id)
    record = record or event_generation
    if record is None:
        return None
    lineages = _find_lineages(
        next_state,
        prior_state,
        record.occurrence_ids,
    )
    if lineages is None:
        return None
    batch_record, reservation = _find_batch(next_state, record.batch_id)
    if batch_record is None:
        batch_record, reservation = _find_batch(prior_state, record.batch_id)
    exact_output_charge = (
        event.exact_output_usage
        if isinstance(event, ReconcileGenerationEvent)
        and event.generation_reservation_id == generation_id
        else None
    )
    return ShadowContextAdmissionTargetRecord(
        target_id=generation_id,
        occurrence_ids=record.occurrence_ids,
        turn_ids=tuple(lineage.turn_id for lineage in lineages),
        tool_call_ids=tuple(lineage.tool_call_id for lineage in lineages),
        producer_instance_ids=tuple(lineage.producer_instance_id for lineage in lineages),
        producer_surfaces=tuple(lineage.producer_surface for lineage in lineages),
        delivery_occurrence_ids=tuple(lineage.delivery_occurrence_id for lineage in lineages),
        reservation_id=(reservation.reservation_id if reservation is not None else None),
        batch_id=record.batch_id,
        generation_reservation_id=generation_id,
        window_epoch_id=record.window_epoch_id,
        reserve_class=record.reserve_class,
        lifecycle_state=record.state,
        proposed_input_count=None,
        generation_allowance=record.maximum_allowance,
        exact_input_charge=None,
        exact_output_charge=exact_output_charge,
        measurement_kind=None,
    )


def _find_batch(
    state: ContextAdmissionState,
    batch_id: AdmissionBatchId,
) -> tuple[AdmissionBatchRecord | None, AdmissionReservation | None]:
    if isinstance(state, ActiveContextAdmissionState):
        record = next(
            (item for item in state.batch_records if item.batch.batch_id == batch_id),
            None,
        )
        if record is not None:
            reservation = next(
                (
                    item
                    for item in state.reservations
                    if item.reservation_id == record.reservation_id
                ),
                None,
            )
            return record, reservation
    for audit in state.closed_epochs:
        record = next(
            (item for item in audit.terminal_batch_records if item.batch.batch_id == batch_id),
            None,
        )
        if record is not None:
            return record, audit.reservation_for(record)
    return None, None


def _find_generation(
    state: ContextAdmissionState,
    generation_id: GenerationReservationId,
) -> GenerationReservationRecord | None:
    if isinstance(state, ActiveContextAdmissionState):
        record = next(
            (
                item
                for item in state.generation_reservations
                if item.generation_reservation_id == generation_id
            ),
            None,
        )
        if record is not None:
            return record
    return next(
        (
            item
            for audit in state.closed_epochs
            for item in audit.terminal_generation_reservations
            if item.generation_reservation_id == generation_id
        ),
        None,
    )


def _find_lineages(
    primary_state: ContextAdmissionState,
    fallback_state: ContextAdmissionState,
    occurrence_ids: tuple[AdmissionOccurrenceId, ...],
) -> tuple[ContextLineage, ...] | None:
    records = {}
    for state in (fallback_state, primary_state):
        if isinstance(state, ActiveContextAdmissionState):
            records.update(
                {
                    record.occurrence.occurrence_id: record.occurrence.lineage
                    for record in state.occurrence_records
                }
            )
        for audit in state.closed_epochs:
            records.update(
                {
                    record.occurrence.occurrence_id: record.occurrence.lineage
                    for record in audit.terminal_occurrence_records
                }
            )
    if any(occurrence_id not in records for occurrence_id in occurrence_ids):
        return None
    return tuple(records[occurrence_id] for occurrence_id in occurrence_ids)


def _prior_occurrence_state(
    state: ContextAdmissionState,
    occurrence_ids: tuple[AdmissionOccurrenceId, ...],
) -> AdmissionState:
    if isinstance(state, ActiveContextAdmissionState):
        states = {
            record.state
            for record in state.occurrence_records
            if record.occurrence.occurrence_id in occurrence_ids
        }
        if len(states) == 1:
            return states.pop()
    return AdmissionState.PROPOSED


def _accounting_status(
    event: ContextAdmissionEvent,
    transition: AdmissionTransition,
) -> ContextAdmissionAccountingStatus:
    if transition.decision.kind is AdmissionDecisionKind.QUARANTINED:
        return ContextAdmissionAccountingStatus.PROTOCOL_QUARANTINED
    if transition.decision.kind in {
        AdmissionDecisionKind.WOULD_REJECT,
        AdmissionDecisionKind.WATERMARK_UNAVAILABLE,
        AdmissionDecisionKind.UPSTREAM_GATED,
        AdmissionDecisionKind.CONFLICT,
        AdmissionDecisionKind.IDEMPOTENCY_EXPIRED,
    }:
        return ContextAdmissionAccountingStatus.SEMANTIC_REJECTION
    if isinstance(
        event,
        MarkIndeterminateEvent | MarkGenerationIndeterminateEvent | RequestReconciliationEvent,
    ):
        return ContextAdmissionAccountingStatus.RECONCILIATION_REQUIRED
    return ContextAdmissionAccountingStatus.RECORDED


def _ignore_fault(fault_point: _LedgerFaultPoint) -> None:
    del fault_point


def _stream_key_bytes(stream_key: ContextAdmissionStreamKey) -> bytes:
    return json.dumps(
        stream_key.to_dict(),
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def _private_file_identity(
    path: Path,
    *,
    owner_id: int,
    reason_code: str,
) -> tuple[int, int]:
    try:
        identity = _read_private_file_identity(
            path,
            owner_id=owner_id,
            file_mode=_DATABASE_MODE,
        )
    except OSError as exc:
        raise _LedgerOpenError(
            ContextAdmissionStorageFailureReason.SECURITY_IDENTITY,
            reason_code,
        ) from exc
    if identity is None:
        raise _LedgerOpenError(
            ContextAdmissionStorageFailureReason.SECURITY_IDENTITY,
            reason_code,
        )
    return identity


def _rollback(connection: sqlite3.Connection) -> None:
    if not connection.in_transaction:
        return
    try:
        connection.execute("ROLLBACK")
    except sqlite3.Error:
        pass


def _sqlite_primary_code(error: sqlite3.Error) -> int | None:
    code = getattr(error, "sqlite_errorcode", None)
    return code & _SQLITE_PRIMARY_MASK if isinstance(code, int) else None


__all__ = ["DefaultContextAdmissionLedger"]
