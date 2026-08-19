"""Contention tests for the crash-safe context-admission ledger.

Part of the test split for issue #4606.
"""

from __future__ import annotations

import sqlite3
from dataclasses import replace
from pathlib import Path

import pytest

import autoskillit.pipeline.context_admission_ledger as ledger_module
from autoskillit.core import (
    ContextAdmissionAccountingStatus,
    ContextAdmissionStorageFailureReason,
    ContextAdmissionStorageHealthStatus,
    ContextThreadId,
)
from autoskillit.pipeline.context_admission_ledger import (
    DefaultContextAdmissionLedger,
)
from tests.fixtures.context_admission import (
    open_event,
    stream_key,
)
from tests.pipeline._context_admission_ledger_helpers import (
    _authority,
)

pytestmark = [pytest.mark.layer("pipeline"), pytest.mark.small]


def test_recovery_remains_incomplete_when_failure_marker_is_contended(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    authority = _authority(tmp_path)
    failed_key = stream_key()
    healthy_key = replace(
        failed_key,
        current_thread_id=ContextThreadId("thread-healthy"),
    )
    ledger = DefaultContextAdmissionLedger(authority)
    assert (
        ledger.apply(failed_key, open_event()).status is ContextAdmissionAccountingStatus.RECORDED
    )
    assert (
        ledger.apply(healthy_key, open_event()).status is ContextAdmissionAccountingStatus.RECORDED
    )
    connection = sqlite3.connect(authority.database_path)
    try:
        connection.execute(
            "DELETE FROM shadow_decisions WHERE stream_id = ?",
            (ledger_module._stream_key_bytes(failed_key),),
        )
        connection.commit()
    finally:
        connection.close()
    recovered = DefaultContextAdmissionLedger(authority)
    monkeypatch.setattr(
        recovered,
        "_persist_stream_failure",
        lambda *_args: False,
    )

    result = recovered.recover_all()

    assert result.status is ContextAdmissionStorageHealthStatus.UNINITIALIZED
    assert recovered.store_health().status is ContextAdmissionStorageHealthStatus.UNINITIALIZED
    assert (
        recovered.stream_health(healthy_key).status
        is ContextAdmissionStorageHealthStatus.UNINITIALIZED
    )
    assert not recovered._stream_health
    assert not recovered._unresolved_streams
    assert not recovered._recovered


@pytest.mark.parametrize("primary_code", [sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED])
def test_recovery_treats_raw_sqlite_contention_as_retryable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    primary_code: int,
) -> None:
    authority = _authority(tmp_path)
    ledger = DefaultContextAdmissionLedger(authority)
    error = sqlite3.OperationalError("busy")
    error.sqlite_errorcode = primary_code

    def raise_contention(_connection: sqlite3.Connection) -> None:
        raise error

    monkeypatch.setattr(
        DefaultContextAdmissionLedger,
        "_validate_integrity",
        staticmethod(raise_contention),
    )

    result = ledger.recover_all()

    assert result.status is ContextAdmissionStorageHealthStatus.UNINITIALIZED
    assert ledger.store_health().status is ContextAdmissionStorageHealthStatus.UNINITIALIZED
    assert not ledger._recovered


def test_apply_retries_when_corruption_failure_marker_is_contended(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    authority = _authority(tmp_path)
    key = stream_key()
    ledger = DefaultContextAdmissionLedger(authority)
    assert ledger.apply(key, open_event()).status is ContextAdmissionAccountingStatus.RECORDED
    connection = sqlite3.connect(authority.database_path)
    try:
        connection.execute(
            "UPDATE streams SET state_envelope = ?",
            (b"invalid",),
        )
        connection.commit()
    finally:
        connection.close()
    persist_stream_failure = ledger._persist_stream_failure
    monkeypatch.setattr(ledger, "_persist_stream_failure", lambda *_args: False)

    contended = ledger.apply(key, open_event())

    assert contended.status is ContextAdmissionAccountingStatus.CONTENDED
    assert contended.reason_code == "busy"
    assert ledger.stream_health(key).status is ContextAdmissionStorageHealthStatus.HEALTHY

    monkeypatch.setattr(ledger, "_persist_stream_failure", persist_stream_failure)
    failed = ledger.apply(key, open_event())

    assert failed.status is ContextAdmissionAccountingStatus.STORAGE_FAIL_CLOSED
    assert ledger.stream_health(key).status is ContextAdmissionStorageHealthStatus.FAIL_CLOSED


def test_busy_begin_is_transient_and_retry_succeeds_without_poisoning_health(
    tmp_path: Path,
) -> None:
    authority = _authority(tmp_path)
    ledger = DefaultContextAdmissionLedger(authority, busy_timeout_ms=0)
    assert ledger.recover_all().status is ContextAdmissionStorageHealthStatus.HEALTHY
    blocker = sqlite3.connect(authority.database_path, isolation_level=None)
    blocker.execute("BEGIN IMMEDIATE")
    try:
        contended = ledger.apply(stream_key(), open_event())
    finally:
        blocker.execute("ROLLBACK")
        blocker.close()

    assert contended.status is ContextAdmissionAccountingStatus.CONTENDED
    assert ledger.store_health().status is ContextAdmissionStorageHealthStatus.HEALTHY
    assert (
        ledger.apply(stream_key(), open_event()).status
        is ContextAdmissionAccountingStatus.RECORDED
    )


@pytest.mark.parametrize("primary_code", [sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED])
@pytest.mark.parametrize(
    ("commit_failures", "busy_timeout_ms", "expected_status", "expected_rows"),
    [
        (1, 50, ContextAdmissionAccountingStatus.RECORDED, 1),
        (None, 0, ContextAdmissionAccountingStatus.CONTENDED, 0),
    ],
)
def test_commit_contention_retries_or_rolls_back_at_deadline(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    primary_code: int,
    commit_failures: int | None,
    busy_timeout_ms: int,
    expected_status: ContextAdmissionAccountingStatus,
    expected_rows: int,
) -> None:
    authority = _authority(tmp_path)
    ledger = DefaultContextAdmissionLedger(
        authority,
        busy_timeout_ms=busy_timeout_ms,
    )
    assert ledger.recover_all().status is ContextAdmissionStorageHealthStatus.HEALTHY
    original_connect = ledger._connect
    wrappers: list[CommitContentionConnection] = []

    class CommitContentionConnection:
        def __init__(self, connection: sqlite3.Connection) -> None:
            self._connection = connection
            self.commit_attempts = 0

        def execute(
            self,
            statement: str,
            parameters: tuple[object, ...] = (),
        ) -> sqlite3.Cursor:
            if statement == "COMMIT":
                self.commit_attempts += 1
                should_fail = commit_failures is None or self.commit_attempts <= commit_failures
                if should_fail:
                    error = sqlite3.OperationalError("commit contended")
                    error.sqlite_errorcode = primary_code
                    raise error
            return self._connection.execute(statement, parameters)

        @property
        def in_transaction(self) -> bool:
            return self._connection.in_transaction

        def close(self) -> None:
            self._connection.close()

    def connect_with_commit_contention() -> CommitContentionConnection:
        wrapper = CommitContentionConnection(original_connect())
        wrappers.append(wrapper)
        return wrapper

    monkeypatch.setattr(ledger, "_connect", connect_with_commit_contention)

    result = ledger.apply(stream_key(), open_event())

    assert result.status is expected_status
    assert wrappers[-1].commit_attempts == (2 if commit_failures == 1 else 1)
    connection = sqlite3.connect(authority.database_path)
    try:
        assert connection.execute("SELECT COUNT(*) FROM journal_events").fetchone() == (
            expected_rows,
        )
    finally:
        connection.close()
    if expected_status is ContextAdmissionAccountingStatus.CONTENDED:
        monkeypatch.setattr(ledger, "_connect", original_connect)
        assert (
            ledger.apply(stream_key(), open_event()).status
            is ContextAdmissionAccountingStatus.RECORDED
        )


def test_apply_stops_when_startup_recovery_is_contended(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    authority = _authority(tmp_path)
    assert (
        DefaultContextAdmissionLedger(authority).recover_all().status
        is ContextAdmissionStorageHealthStatus.HEALTHY
    )
    ledger = DefaultContextAdmissionLedger(authority, busy_timeout_ms=0)
    blocker = sqlite3.connect(authority.database_path, isolation_level=None)
    blocker.execute("BEGIN EXCLUSIVE")
    original_recover_all = ledger.recover_all
    lock_released = False

    def recover_then_release_lock() -> object:
        nonlocal lock_released
        result = original_recover_all()
        if not lock_released:
            blocker.execute("ROLLBACK")
            blocker.close()
            lock_released = True
        return result

    monkeypatch.setattr(ledger, "recover_all", recover_then_release_lock)

    contended = ledger.apply(stream_key(), open_event())

    assert contended.status is ContextAdmissionAccountingStatus.CONTENDED
    assert (
        ledger.apply(stream_key(), open_event()).status
        is ContextAdmissionAccountingStatus.RECORDED
    )


def test_inspection_stops_when_startup_recovery_is_contended(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    authority = _authority(tmp_path)
    key = stream_key()
    assert (
        DefaultContextAdmissionLedger(authority).apply(key, open_event()).status
        is ContextAdmissionAccountingStatus.RECORDED
    )
    ledger = DefaultContextAdmissionLedger(authority, busy_timeout_ms=0)
    blocker = sqlite3.connect(authority.database_path, isolation_level=None)
    blocker.execute("BEGIN EXCLUSIVE")
    original_recover_all = ledger.recover_all
    lock_released = False

    def recover_then_release_lock() -> object:
        nonlocal lock_released
        result = original_recover_all()
        if not lock_released:
            blocker.execute("ROLLBACK")
            blocker.close()
            lock_released = True
        return result

    monkeypatch.setattr(ledger, "recover_all", recover_then_release_lock)

    contended = ledger.inspect_stream(key)

    assert contended.health.status is ContextAdmissionStorageHealthStatus.UNINITIALIZED
    assert ledger.inspect_stream(key).health.status is ContextAdmissionStorageHealthStatus.HEALTHY


@pytest.mark.parametrize(
    "primary_code",
    [
        sqlite3.SQLITE_FULL,
        sqlite3.SQLITE_IOERR,
        sqlite3.SQLITE_IOERR_READ,
        sqlite3.SQLITE_INTERRUPT,
        sqlite3.SQLITE_NOMEM,
    ],
)
def test_inspection_fails_closed_on_noncontention_sqlite_failures(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    primary_code: int,
) -> None:
    authority = _authority(tmp_path)
    key = stream_key()
    ledger = DefaultContextAdmissionLedger(authority)
    assert ledger.apply(key, open_event()).status is ContextAdmissionAccountingStatus.RECORDED
    original_connect = ledger._connect
    error = sqlite3.OperationalError("transient-inspection-failure")
    error.sqlite_errorcode = primary_code

    def raise_transient_error() -> sqlite3.Connection:
        raise error

    monkeypatch.setattr(ledger, "_connect", raise_transient_error)

    failed = ledger.inspect_stream(key)

    assert failed.health.status is ContextAdmissionStorageHealthStatus.FAIL_CLOSED
    assert failed.health.failure_reason is ContextAdmissionStorageFailureReason.IO
    assert failed.health.reason_code == "sqlite-inspection-failed"
    assert ledger.store_health().status is ContextAdmissionStorageHealthStatus.FAIL_CLOSED
    monkeypatch.setattr(ledger, "_connect", original_connect)
    assert (
        ledger.inspect_stream(key).health.status is ContextAdmissionStorageHealthStatus.FAIL_CLOSED
    )


@pytest.mark.parametrize(
    ("fault_name", "expected_status"),
    [
        ("before_commit", ContextAdmissionAccountingStatus.CONTENDED),
        ("after_commit", ContextAdmissionAccountingStatus.EXACT_REPLAY),
    ],
)
@pytest.mark.parametrize(
    "sqlite_code",
    [
        sqlite3.SQLITE_FULL,
        sqlite3.SQLITE_IOERR,
        sqlite3.SQLITE_IOERR_READ,
        sqlite3.SQLITE_INTERRUPT,
        sqlite3.SQLITE_NOMEM,
    ],
)
def test_sqlite_result_class_recovery_reopens_and_resolves_publication(
    tmp_path: Path,
    fault_name: str,
    expected_status: ContextAdmissionAccountingStatus,
    sqlite_code: int,
) -> None:
    authority = _authority(tmp_path)
    fired = False

    def inject(point: object) -> None:
        nonlocal fired
        if not fired and getattr(point, "value") == fault_name:
            fired = True
            error = sqlite3.OperationalError(fault_name)
            error.sqlite_errorcode = sqlite_code
            raise error

    ledger = DefaultContextAdmissionLedger(
        authority,
        fault_callback=inject,
    )
    event = open_event()
    result = ledger.apply(stream_key(), event)

    assert result.status is expected_status
    expected_journal_sequence = 1 if fault_name == "after_commit" else None
    assert result.journal_sequence == expected_journal_sequence
    connection = sqlite3.connect(authority.database_path)
    try:
        expected_rows = 1 if fault_name == "after_commit" else 0
        assert connection.execute("SELECT COUNT(*) FROM journal_events").fetchone() == (
            expected_rows,
        )
    finally:
        connection.close()
    if fault_name == "before_commit":
        assert (
            ledger.apply(stream_key(), event).status is ContextAdmissionAccountingStatus.RECORDED
        )


def test_inspection_contention_is_transient_and_does_not_poison_health(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ledger = DefaultContextAdmissionLedger(_authority(tmp_path))
    key = stream_key()
    assert ledger.apply(key, open_event()).status is ContextAdmissionAccountingStatus.RECORDED
    original_connect = ledger._connect
    fired = False

    class BusyOnceConnection:
        def __init__(self, connection: sqlite3.Connection) -> None:
            self._connection = connection

        def execute(
            self,
            statement: str,
            parameters: tuple[object, ...] = (),
        ) -> sqlite3.Cursor:
            nonlocal fired
            if not fired and "FROM streams WHERE stream_id" in statement:
                fired = True
                error = sqlite3.OperationalError("busy")
                error.sqlite_errorcode = sqlite3.SQLITE_BUSY
                raise error
            return self._connection.execute(statement, parameters)

        @property
        def in_transaction(self) -> bool:
            return self._connection.in_transaction

        def setlimit(self, category: int, limit: int) -> int:
            return self._connection.setlimit(category, limit)

        def close(self) -> None:
            self._connection.close()

    monkeypatch.setattr(ledger, "_connect", lambda: BusyOnceConnection(original_connect()))

    contended = ledger.inspect_stream(key)

    assert contended.health.status is ContextAdmissionStorageHealthStatus.UNINITIALIZED
    assert ledger.stream_health(key).status is ContextAdmissionStorageHealthStatus.HEALTHY
    assert ledger.inspect_stream(key).health.status is ContextAdmissionStorageHealthStatus.HEALTHY


def test_inspection_retries_when_failure_marker_is_contended(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    authority = _authority(tmp_path)
    key = stream_key()
    ledger = DefaultContextAdmissionLedger(authority)
    assert ledger.apply(key, open_event()).status is ContextAdmissionAccountingStatus.RECORDED
    connection = sqlite3.connect(authority.database_path)
    try:
        connection.execute(
            "UPDATE journal_events SET event_envelope = ?",
            (b"invalid",),
        )
        connection.commit()
    finally:
        connection.close()
    monkeypatch.setattr(ledger, "_persist_stream_failure", lambda *_args: False)

    inspection = ledger.inspect_stream(key)

    assert inspection.health.status is ContextAdmissionStorageHealthStatus.UNINITIALIZED
    assert ledger.stream_health(key).status is ContextAdmissionStorageHealthStatus.HEALTHY
