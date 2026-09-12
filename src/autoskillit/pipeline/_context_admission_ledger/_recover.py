"""Recovery orchestration and reusable stream-failure persistence."""

from __future__ import annotations

import sqlite3
from typing import cast

from autoskillit.core import (
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
from ._store import _LedgerStore

logger = get_logger(__name__)

__all__ = ["_LedgerRecovery"]


class _LedgerRecovery(_LedgerStore):
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
                connection, read_budget = _LedgerRecovery._prepare_recovery_state(self)
                self._stream_health.clear()
                self._unresolved_streams.clear()
                stream_rows = cast(
                    tuple[
                        tuple[
                            bytes,
                            bytes,
                            bytes,
                            bytes,
                            int,
                            int,
                            int,
                            str,
                            str | None,
                            str | None,
                        ],
                        ...,
                    ],
                    _read_bounded_rows(
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
                    ),
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
                _LedgerRecovery._commit_recovery(
                    self,
                    connection,
                    pending_stream_failures,
                )
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

    def _prepare_recovery_state(self) -> tuple[sqlite3.Connection, _LedgerReadBudget]:
        """Open the store, begin recovery, and validate schema and metadata."""
        self._ensure_store()
        connection = self._connect()
        try:
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
            metadata_rows = cast(
                tuple[tuple[str, str], ...],
                _read_bounded_rows(
                    connection.execute("SELECT key, value FROM metadata"),
                    read_budget,
                ),
            )
            self._validate_metadata(dict(metadata_rows))
            _preflight_storage_routes(connection, read_budget)
            return connection, read_budget
        except BaseException:
            connection.close()
            raise

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
                if (
                    self._store_health.status
                    is not ContextAdmissionStorageHealthStatus.FAIL_CLOSED
                ):
                    raise _LedgerContended
                break
        if self._store_health.status is not ContextAdmissionStorageHealthStatus.FAIL_CLOSED:
            self._store_health = ContextAdmissionStoreHealth(
                ContextAdmissionStorageHealthStatus.HEALTHY
            )
        self._recovered = True

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
            logger.warning(
                "context-admission stream-health persistence failed code=%s: %s",
                _sqlite_primary_code(exc),
                exc,
            )
            self._set_store_failure(
                ContextAdmissionStorageFailureReason.IO,
                f"stream-health-persistence-failed:{_sqlite_primary_code(exc)}",
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
