"""Recovery orchestration: full-store recovery and apply-time mid-flight recovery.

Owns ``recover_all`` (the full-store re-projection walk) and
``_recover_sqlite_result`` (the apply-time mid-flight rollback + re-recovery
handler).

Wavefront 1 of #4667.
"""

from __future__ import annotations

import sqlite3

from autoskillit.core import (
    ContextAdmissionAccountingResult,
    ContextAdmissionAccountingStatus,
    ContextAdmissionEvent,
    ContextAdmissionRecoveryResult,
    ContextAdmissionStorageFailureReason,
    ContextAdmissionStorageHealthStatus,
    ContextAdmissionStoreHealth,
    ContextAdmissionStreamHealth,
    ContextAdmissionStreamKey,
    ContextAdmissionValidationError,
    get_logger,
)

from ._codec import _decode_stream_key, _stream_key_bytes
from ._projection import (
    _MAX_RECOVERY_BYTES,
    _MAX_RECOVERY_ROWS,
    _recover_stream_projection,
    _stored_stream_health,
)
from ._sqlite_errors import (
    _SQLITE_BUSY_CODES,
    _LedgerContended,
    _rollback,
    _sqlite_primary_code,
)
from ._state_queries import _state_has_unresolved_work
from ._storage import (
    _LedgerOpenError,
    _LedgerReadBudget,
    _preflight_storage_routes,
    _read_bounded_rows,
)

logger = get_logger(__name__)

__all__ = ["recover_all", "_recover_sqlite_result", "_set_store_failure"]


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
            connection, read_budget = _prepare_recovery_state(self)
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
                health = _stored_stream_health(stream_key, row[7], row[8], row[9])
                if health.status is ContextAdmissionStorageHealthStatus.FAIL_CLOSED:
                    self._stream_health[stream_key] = health
                    continue
                if health.status is not ContextAdmissionStorageHealthStatus.HEALTHY:
                    pending_stream_failures.append(
                        (
                            stream_id,
                            stream_key,
                            ContextAdmissionStorageFailureReason.REPLAY_MISMATCH,
                            "invalid-stream-health",
                        )
                    )
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
                    )[0]
                except ContextAdmissionValidationError as exc:
                    logger.debug("context-admission replay decode failed: %s", exc)
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
            _commit_recovery(self, connection, pending_stream_failures)
        except _LedgerContended as exc:
            logger.debug("context-admission recovery contended: %s", exc)
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


def _prepare_recovery_state(
    self,
) -> tuple[sqlite3.Connection, _LedgerReadBudget]:
    """Open the store, begin the recovery transaction, validate schema/metadata.

    Returns ``(connection, read_budget)`` for the per-stream walk that
    follows.
    """
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
    return connection, read_budget


def _commit_recovery(
    self,
    connection: sqlite3.Connection,
    pending_stream_failures: list[
        tuple[
            bytes,
            ContextAdmissionStreamKey,
            ContextAdmissionStorageFailureReason,
            str,
        ]
    ],
) -> None:
    """Persist pending failure records and flip the ledger to HEALTHY."""
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
            if self._store_health.status is not ContextAdmissionStorageHealthStatus.FAIL_CLOSED:
                raise _LedgerContended
            break
    if self._store_health.status is not ContextAdmissionStorageHealthStatus.FAIL_CLOSED:
        self._store_health = ContextAdmissionStoreHealth(
            ContextAdmissionStorageHealthStatus.HEALTHY
        )
    self._recovered = True


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


def _set_store_failure(
    self,
    reason: ContextAdmissionStorageFailureReason,
    reason_code: str,
) -> None:
    """Instance method bound onto DefaultContextAdmissionLedger.

    Owns the FAIL_CLOSED store-health transition. Sibling HEALTHY and
    UNINITIALIZED transitions are inlined in ``recover_all`` above; this
    wrapper is the only place where the ledger flips to FAIL_CLOSED.
    """
    self._store_health = ContextAdmissionStoreHealth(
        ContextAdmissionStorageHealthStatus.FAIL_CLOSED,
        failure_reason=reason,
        reason_code=reason_code,
    )
    self._recovered = True
