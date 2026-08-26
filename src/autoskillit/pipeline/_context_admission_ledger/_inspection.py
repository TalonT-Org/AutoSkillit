"""Inspection helpers and the inspect_stream body.

Owns the value-constructors :func:`_empty_inspection` and
:func:`_contended_inspection`, plus the ``inspect_stream`` body refactored
into a standalone ``_inspect_stream(self, stream_key)`` function that is
rebound onto :class:`DefaultContextAdmissionLedger` from ``__init__.py``.

Wavefront 1 of #4667.
"""

from __future__ import annotations

import sqlite3
from typing import Any, cast

from autoskillit.core import (
    ContextAdmissionInspectionResult,
    ContextAdmissionStorageFailureReason,
    ContextAdmissionStorageHealthStatus,
    ContextAdmissionStreamHealth,
    ContextAdmissionStreamKey,
    ContextAdmissionValidationError,
)

from ._codec import _stream_key_bytes
from ._projection import (
    _MAX_RECOVERY_BYTES,
    _MAX_RECOVERY_ROWS,
    _LedgerReadBudget,
    _recover_stream_projection,
    _stored_stream_health,
)
from ._sqlite_errors import (
    _SQLITE_BUSY_CODES,
    _LedgerContended,
    _rollback,
    _sqlite_primary_code,
)
from ._storage import _LedgerOpenError

__all__ = [
    "_empty_inspection",
    "_contended_inspection",
    "_inspect_stream",
]


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


def _inspect_stream(
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
        connection = None
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
                SELECT stream_key, genesis_envelope, state_envelope,
                       aggregate_revision, admission_sequence,
                       latest_journal_sequence, health_status,
                       failure_reason, reason_code
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
            health = _stored_stream_health(stream_key, row[6], row[7], row[8])
            if health.status is ContextAdmissionStorageHealthStatus.FAIL_CLOSED:
                self._stream_health[stream_key] = health
                return _empty_inspection(stream_key, health)
            projection = _recover_stream_projection(
                connection,
                stream_id,
                stream_key,
                genesis_envelope=bytes(row[1]),
                materialized_state_envelope=bytes(row[2]),
                aggregate_revision=int(row[3]),
                admission_sequence=int(row[4]),
                latest_journal_sequence=int(row[5]),
                read_budget=read_budget,
            )
            inspection = ContextAdmissionInspectionResult(
                stream_key=stream_key,
                health=health,
                state=projection[0],
                events=projection[1],
                decisions=projection[2],
                effects=projection[3],
                shadows=projection[4],
                latest_journal_sequence=int(row[5]),
            )
            self._stream_health[stream_key] = health
            return inspection
        except _LedgerContended:
            return _contended_inspection(stream_key)
        except sqlite3.Error as exc:
            primary_code = _sqlite_primary_code(exc)
            if connection is not None:
                _rollback(connection)
            if primary_code in _SQLITE_BUSY_CODES:
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
            if connection is None and isinstance(exc, _LedgerOpenError):
                self._set_store_failure(reason, reason_code)
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
                if self._store_health.status is ContextAdmissionStorageHealthStatus.FAIL_CLOSED:
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
