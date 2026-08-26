"""Apply-time recovery orchestration.

Owns ``_recover_sqlite_result``, the post-error mid-flight recovery handler
that rolls back a failed apply transaction, resets recovered state, calls
``self.recover_all()`` and ``self.inspect_stream()``, and re-enters
``self.apply()`` if the recovered projection already contains the event being
replayed. Extracted from ``_apply`` to localize the cross-shard orchestration
in a single shard.

Wavefront 1 of #4667.
"""

from __future__ import annotations

import sqlite3

from autoskillit.core import (
    ContextAdmissionAccountingResult,
    ContextAdmissionAccountingStatus,
    ContextAdmissionEvent,
    ContextAdmissionStorageFailureReason,
    ContextAdmissionStorageHealthStatus,
    ContextAdmissionStoreHealth,
    ContextAdmissionStreamKey,
)

from ._sqlite_errors import _rollback


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


__all__ = ["_recover_sqlite_result"]
