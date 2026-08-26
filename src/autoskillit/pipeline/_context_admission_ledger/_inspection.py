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
    ContextAdmissionStoreHealth,
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


def _fail_closed_inspection(
    stream_key: ContextAdmissionStreamKey,
    store_health: ContextAdmissionStoreHealth,
) -> ContextAdmissionInspectionResult:
    """Build a fail-closed inspection whose health mirrors ``store_health``."""
    return _empty_inspection(
        stream_key,
        ContextAdmissionStreamHealth(
            stream_key,
            ContextAdmissionStorageHealthStatus.FAIL_CLOSED,
            failure_reason=store_health.failure_reason,
            reason_code=store_health.reason_code,
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
            return _fail_closed_inspection(stream_key, self._store_health)
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
            row = _load_stream_row(connection, stream_id, read_budget)
            if row is None:
                return _empty_inspection(
                    stream_key,
                    ContextAdmissionStreamHealth(
                        stream_key,
                        ContextAdmissionStorageHealthStatus.UNINITIALIZED,
                    ),
                )
            health = _stored_stream_health(stream_key, row[6], row[7], row[8])
            if health.status is ContextAdmissionStorageHealthStatus.FAIL_CLOSED:
                self._stream_health[stream_key] = health
                return _empty_inspection(stream_key, health)
            return _decode_inspection_projection(
                self, connection, stream_id, stream_key, row, health, read_budget
            )
        except _LedgerContended:
            return _contended_inspection(stream_key)
        except sqlite3.Error as exc:
            return _map_sqlite_inspection_failure(self, stream_key, connection, exc)
        except (ContextAdmissionValidationError, _LedgerOpenError) as exc:
            return _map_persistent_inspection_failure(self, stream_key, stream_id, connection, exc)
        finally:
            if connection is not None:
                connection.close()


def _load_stream_row(
    connection: sqlite3.Connection,
    stream_id: bytes,
    read_budget: _LedgerReadBudget,
) -> Any:
    """Fetch the streams row for ``stream_id`` and enforce identity.

    Returns ``None`` when no row exists for the stream (caller decides
    how to surface an empty inspection). Raises :class:`_LedgerOpenError`
    when the persisted stream_key does not round-trip to ``stream_id``.
    """
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
        return None
    row = read_budget.consume(cast(tuple[Any, ...], row))
    if bytes(row[0]) != stream_id:
        raise _LedgerOpenError(
            ContextAdmissionStorageFailureReason.IDENTITY_MISMATCH,
            "stream-key-mismatch",
        )
    return row


def _decode_inspection_projection(
    self,
    connection: sqlite3.Connection,
    stream_id: bytes,
    stream_key: ContextAdmissionStreamKey,
    row: Any,
    health: ContextAdmissionStreamHealth,
    read_budget: _LedgerReadBudget,
) -> ContextAdmissionInspectionResult:
    """Replay the projection and assemble the inspection result."""
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


def _map_sqlite_inspection_failure(
    self,
    stream_key: ContextAdmissionStreamKey,
    connection: sqlite3.Connection | None,
    exc: sqlite3.Error,
) -> ContextAdmissionInspectionResult:
    """Translate a ``sqlite3.Error`` during inspection into an inspection result."""
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
        return _fail_closed_inspection(stream_key, self._store_health)
    reason = (
        ContextAdmissionStorageFailureReason.INTEGRITY
        if primary_code in {sqlite3.SQLITE_CORRUPT, sqlite3.SQLITE_CONSTRAINT}
        else ContextAdmissionStorageFailureReason.IO
    )
    self._set_store_failure(reason, "sqlite-inspection-failed")
    return _fail_closed_inspection(stream_key, self._store_health)


def _map_persistent_inspection_failure(
    self,
    stream_key: ContextAdmissionStreamKey,
    stream_id: bytes,
    connection: sqlite3.Connection | None,
    exc: ContextAdmissionValidationError | _LedgerOpenError,
) -> ContextAdmissionInspectionResult:
    """Translate a decode/identity failure into a fail-closed or contended inspection."""
    reason = (
        exc.reason
        if isinstance(exc, _LedgerOpenError)
        else ContextAdmissionStorageFailureReason.REPLAY_MISMATCH
    )
    reason_code = (
        exc.reason_code if isinstance(exc, _LedgerOpenError) else "inspection-decode-failed"
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
            return _fail_closed_inspection(stream_key, self._store_health)
        return _contended_inspection(stream_key)
    return _empty_inspection(
        stream_key,
        self.stream_health(stream_key),
    )
