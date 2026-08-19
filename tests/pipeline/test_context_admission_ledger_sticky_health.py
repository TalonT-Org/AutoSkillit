"""Sticky health tests for the crash-safe context-admission ledger.

Part of the test split for issue #4606.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import replace
from pathlib import Path

import pytest

import autoskillit.pipeline.context_admission_ledger as ledger_module
from autoskillit.core import (
    ContextAdmissionAccountingStatus,
    ContextAdmissionStorageFailureReason,
    ContextAdmissionStorageHealthStatus,
    ContextAdmissionStreamKey,
    ContextThreadId,
)
from autoskillit.pipeline.context_admission_ledger import (
    DefaultContextAdmissionLedger,
)
from tests.fixtures.context_admission import (
    occurrence,
    open_event,
    propose_event,
    stream_key,
)
from tests.pipeline._context_admission_ledger_helpers import (
    _authority,
)

pytestmark = [pytest.mark.layer("pipeline"), pytest.mark.small]


def test_recovery_failure_is_sticky_per_stream_and_isolates_other_streams(
    tmp_path: Path,
) -> None:
    authority = _authority(tmp_path)
    failed_key = stream_key()
    healthy_key = replace(
        failed_key,
        current_thread_id=ContextThreadId("thread-other"),
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
        rows = connection.execute("SELECT stream_id FROM streams ORDER BY stream_id").fetchall()
        failed_id = next(
            bytes(row[0])
            for row in rows
            if ContextAdmissionStreamKey.from_dict(json.loads(bytes(row[0]).decode("utf-8")))
            == failed_key
        )
        connection.execute(
            """
            DELETE FROM shadow_decisions
            WHERE stream_id = ? AND journal_sequence = 1
            """,
            (failed_id,),
        )
        connection.commit()
    finally:
        connection.close()

    recovered = DefaultContextAdmissionLedger(authority)
    result = recovered.recover_all()

    assert result.status is ContextAdmissionStorageHealthStatus.HEALTHY
    assert recovered.stream_health(failed_key).status is (
        ContextAdmissionStorageHealthStatus.FAIL_CLOSED
    )
    assert recovered.stream_health(healthy_key).status is (
        ContextAdmissionStorageHealthStatus.HEALTHY
    )
    assert (
        recovered.apply(failed_key, open_event()).status
        is ContextAdmissionAccountingStatus.STORAGE_FAIL_CLOSED
    )
    assert (
        recovered.apply(healthy_key, open_event()).status
        is ContextAdmissionAccountingStatus.EXACT_REPLAY
    )
    restarted = DefaultContextAdmissionLedger(authority)
    restarted.recover_all()
    assert restarted.stream_health(failed_key).status is (
        ContextAdmissionStorageHealthStatus.FAIL_CLOSED
    )


def test_apply_persists_sticky_failure_for_corrupt_materialized_state(
    tmp_path: Path,
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

    failed = ledger.apply(key, open_event())

    assert failed.status is ContextAdmissionAccountingStatus.STORAGE_FAIL_CLOSED
    assert failed.failure_reason is ContextAdmissionStorageFailureReason.REPLAY_MISMATCH
    assert failed.reason_code == "stored-state-decode-failed"
    assert ledger.stream_health(key).status is ContextAdmissionStorageHealthStatus.FAIL_CLOSED
    restarted = DefaultContextAdmissionLedger(authority)
    restarted.recover_all()
    assert restarted.store_health().status is ContextAdmissionStorageHealthStatus.FAIL_CLOSED
    assert (
        restarted.apply(key, open_event()).status
        is ContextAdmissionAccountingStatus.STORAGE_FAIL_CLOSED
    )


@pytest.mark.parametrize(
    ("status", "failure_reason", "reason_code"),
    [
        ("unknown", None, None),
        (
            ContextAdmissionStorageHealthStatus.HEALTHY.value,
            ContextAdmissionStorageFailureReason.IO.value,
            "inconsistent-health",
        ),
    ],
)
def test_apply_rejects_invalid_persisted_stream_health(
    tmp_path: Path,
    status: str,
    failure_reason: str | None,
    reason_code: str | None,
) -> None:
    authority = _authority(tmp_path)
    key = stream_key()
    ledger = DefaultContextAdmissionLedger(authority)
    assert ledger.apply(key, open_event()).status is ContextAdmissionAccountingStatus.RECORDED
    connection = sqlite3.connect(authority.database_path)
    try:
        connection.execute(
            """
            UPDATE streams
            SET health_status = ?, failure_reason = ?, reason_code = ?
            """,
            (status, failure_reason, reason_code),
        )
        connection.commit()
    finally:
        connection.close()

    failed = ledger.apply(key, open_event())

    assert failed.status is ContextAdmissionAccountingStatus.STORAGE_FAIL_CLOSED
    assert failed.failure_reason is ContextAdmissionStorageFailureReason.REPLAY_MISMATCH
    assert failed.reason_code == "invalid-stream-health"


def test_inspection_rejects_health_corrupted_after_recovery(tmp_path: Path) -> None:
    authority = _authority(tmp_path)
    key = stream_key()
    ledger = DefaultContextAdmissionLedger(authority)
    assert ledger.apply(key, open_event()).status is ContextAdmissionAccountingStatus.RECORDED
    connection = sqlite3.connect(authority.database_path)
    try:
        connection.execute("UPDATE streams SET health_status = 'unknown'")
        connection.commit()
    finally:
        connection.close()

    inspection = ledger.inspect_stream(key)

    assert inspection.health.status is ContextAdmissionStorageHealthStatus.FAIL_CLOSED
    assert inspection.health.failure_reason is ContextAdmissionStorageFailureReason.INTEGRITY
    assert inspection.health.reason_code == "invalid-stream-health"


def test_recovery_rejects_uninitialized_health_for_bound_stream(tmp_path: Path) -> None:
    authority = _authority(tmp_path)
    key = stream_key()
    assert (
        DefaultContextAdmissionLedger(authority).apply(key, open_event()).status
        is ContextAdmissionAccountingStatus.RECORDED
    )
    connection = sqlite3.connect(authority.database_path)
    try:
        connection.execute(
            "UPDATE streams SET health_status = ?, failure_reason = NULL, reason_code = NULL",
            (ContextAdmissionStorageHealthStatus.UNINITIALIZED.value,),
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
    assert health.reason_code == "invalid-stream-health"


@pytest.mark.parametrize("operation", ["apply", "inspect"])
def test_post_recovery_open_failure_sets_sticky_store_health(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    operation: str,
) -> None:
    authority = _authority(tmp_path)
    key = stream_key()
    ledger = DefaultContextAdmissionLedger(authority)
    assert ledger.apply(key, open_event()).status is ContextAdmissionAccountingStatus.RECORDED

    def raise_permanent_open_failure() -> sqlite3.Connection:
        raise ledger_module._LedgerOpenError(
            ContextAdmissionStorageFailureReason.CONFIGURATION,
            "post-recovery-open-failed",
        )

    monkeypatch.setattr(ledger, "_connect", raise_permanent_open_failure)

    if operation == "apply":
        result = ledger.apply(key, open_event())
        assert result.status is ContextAdmissionAccountingStatus.STORAGE_FAIL_CLOSED
    else:
        inspection = ledger.inspect_stream(key)
        assert inspection.health.status is ContextAdmissionStorageHealthStatus.FAIL_CLOSED

    health = ledger.store_health()
    assert health.status is ContextAdmissionStorageHealthStatus.FAIL_CLOSED
    assert health.failure_reason is ContextAdmissionStorageFailureReason.CONFIGURATION
    assert health.reason_code == "post-recovery-open-failed"


def test_lineage_mismatch_sets_sticky_stream_health(tmp_path: Path) -> None:
    ledger = DefaultContextAdmissionLedger(_authority(tmp_path))
    key = stream_key()
    opened = ledger.apply(key, open_event())
    assert opened.transition is not None
    value = occurrence()
    mismatched_lineage = replace(
        value.lineage,
        current_thread_id=ContextThreadId("thread-other"),
    )
    event = propose_event(
        opened.transition.next_state,
        replace(value, lineage=mismatched_lineage),
    )

    result = ledger.apply(key, event)

    assert result.status is ContextAdmissionAccountingStatus.STORAGE_FAIL_CLOSED
    assert result.failure_reason is ContextAdmissionStorageFailureReason.IDENTITY_MISMATCH
    assert ledger.stream_health(key).status is ContextAdmissionStorageHealthStatus.FAIL_CLOSED
