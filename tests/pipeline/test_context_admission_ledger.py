"""Crash-safe context-admission ledger tests."""

from __future__ import annotations

import os
import sqlite3
import stat
from dataclasses import replace
from pathlib import Path

import pytest

from autoskillit.core import (
    AdmissionDecisionKind,
    ContextAdmissionAccountingStatus,
    ContextAdmissionStorageFailureReason,
    ContextAdmissionStorageHealthStatus,
    ContextAdmissionStoreAuthority,
    ContextThreadId,
)
from autoskillit.pipeline.context_admission_ledger import (
    DefaultContextAdmissionLedger,
)
from tests.fixtures.context_admission import (
    batch,
    occurrence,
    open_event,
    propose_event,
    reserve_event,
    snapshot,
    stream_key,
)

pytestmark = [pytest.mark.layer("pipeline"), pytest.mark.small]


def _authority(tmp_path: Path) -> ContextAdmissionStoreAuthority:
    return ContextAdmissionStoreAuthority(
        database_path=tmp_path / "context-admission" / "ledger.sqlite3",
        expected_owner_id=os.getuid(),
    )


def test_construction_is_side_effect_free_and_recovery_publishes_private_schema(
    tmp_path: Path,
) -> None:
    authority = _authority(tmp_path)
    ledger = DefaultContextAdmissionLedger(authority)
    assert not authority.database_path.exists()
    assert ledger.store_health().status is ContextAdmissionStorageHealthStatus.UNINITIALIZED

    recovered = ledger.recover_all()

    assert recovered.status is ContextAdmissionStorageHealthStatus.HEALTHY
    assert stat.S_IMODE(authority.database_path.parent.stat().st_mode) == 0o700
    database_stat = authority.database_path.stat()
    assert stat.S_ISREG(database_stat.st_mode)
    assert stat.S_IMODE(database_stat.st_mode) == 0o600
    assert database_stat.st_nlink == 1
    connection = sqlite3.connect(authority.database_path)
    try:
        tables = {
            row[0]
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
        }
        assert tables == {
            "metadata",
            "streams",
            "journal_events",
            "effect_outbox",
            "shadow_decisions",
        }
        assert connection.execute("PRAGMA journal_mode").fetchone() == ("delete",)
        assert connection.execute("PRAGMA integrity_check").fetchone() == ("ok",)
    finally:
        connection.close()
    assert not list(authority.database_path.parent.glob("*.tmp*"))


def test_each_ledger_connection_sets_and_reads_back_required_pragmas(
    tmp_path: Path,
) -> None:
    ledger = DefaultContextAdmissionLedger(_authority(tmp_path), busy_timeout_ms=37)
    assert ledger.recover_all().status is ContextAdmissionStorageHealthStatus.HEALTHY

    connection = ledger._connect()
    try:
        assert connection.execute("PRAGMA journal_mode").fetchone() == ("delete",)
        assert connection.execute("PRAGMA synchronous").fetchone() == (3,)
        assert connection.execute("PRAGMA foreign_keys").fetchone() == (1,)
        assert connection.execute("PRAGMA busy_timeout").fetchone() == (37,)
    finally:
        connection.close()


def test_insecure_existing_parent_fails_closed_without_repair(
    tmp_path: Path,
) -> None:
    authority = _authority(tmp_path)
    authority.database_path.parent.mkdir(mode=0o755)
    authority.database_path.parent.chmod(0o755)

    result = DefaultContextAdmissionLedger(authority).recover_all()

    assert result.status is ContextAdmissionStorageHealthStatus.FAIL_CLOSED
    assert (
        result.store_health.failure_reason
        is ContextAdmissionStorageFailureReason.SECURITY_IDENTITY
    )
    assert stat.S_IMODE(authority.database_path.parent.stat().st_mode) == 0o755
    assert not authority.database_path.exists()


def test_preexisting_database_symlink_fails_closed_without_following_it(
    tmp_path: Path,
) -> None:
    authority = _authority(tmp_path)
    authority.database_path.parent.mkdir(mode=0o700)
    target = tmp_path / "outside.sqlite3"
    target.write_text("canary", encoding="utf-8")
    authority.database_path.symlink_to(target)

    result = DefaultContextAdmissionLedger(authority).recover_all()

    assert result.status is ContextAdmissionStorageHealthStatus.FAIL_CLOSED
    assert target.read_text(encoding="utf-8") == "canary"


def test_preexisting_sidecar_target_fails_closed_before_initialization(
    tmp_path: Path,
) -> None:
    authority = _authority(tmp_path)
    authority.database_path.parent.mkdir(mode=0o700)
    sidecar = Path(f"{authority.database_path}-journal")
    sidecar.write_bytes(b"untrusted")
    sidecar.chmod(0o600)

    result = DefaultContextAdmissionLedger(authority).recover_all()

    assert result.status is ContextAdmissionStorageHealthStatus.FAIL_CLOSED
    assert not authority.database_path.exists()
    assert sidecar.read_bytes() == b"untrusted"


def test_incomplete_existing_database_is_not_reinitialized(
    tmp_path: Path,
) -> None:
    authority = _authority(tmp_path)
    authority.database_path.parent.mkdir(mode=0o700)
    authority.database_path.touch(mode=0o600)

    result = DefaultContextAdmissionLedger(authority).recover_all()

    assert result.status is ContextAdmissionStorageHealthStatus.FAIL_CLOSED
    assert result.store_health.failure_reason in {
        ContextAdmissionStorageFailureReason.IO,
        ContextAdmissionStorageFailureReason.INTEGRITY,
    }
    assert authority.database_path.stat().st_size == 0


def test_recovery_is_idempotent_for_an_empty_healthy_store(tmp_path: Path) -> None:
    ledger = DefaultContextAdmissionLedger(_authority(tmp_path))
    first = ledger.recover_all()
    second = ledger.recover_all()
    assert first == second


def test_reducer_transition_is_published_atomically_with_independent_journal_order(
    tmp_path: Path,
) -> None:
    authority = _authority(tmp_path)
    ledger = DefaultContextAdmissionLedger(authority)
    key = stream_key()

    opened = ledger.apply(key, open_event())
    assert opened.status is ContextAdmissionAccountingStatus.RECORDED
    assert opened.journal_sequence == 1
    assert opened.transition is not None
    occurrence_value = occurrence()
    proposed = ledger.apply(
        key,
        propose_event(opened.transition.next_state, occurrence_value),
    )
    assert proposed.journal_sequence == 2
    assert proposed.transition is not None
    reserved = ledger.reserve(
        key,
        reserve_event(
            proposed.transition.next_state,
            batch(occurrence_value),
            occurrence_value,
        ),
    )
    assert reserved.status is ContextAdmissionAccountingStatus.RECORDED
    assert reserved.journal_sequence == 3
    assert reserved.transition is not None
    assert reserved.transition.next_state.admission_sequence.value == 1

    connection = sqlite3.connect(authority.database_path)
    try:
        assert connection.execute("SELECT COUNT(*) FROM journal_events").fetchone() == (3,)
        assert connection.execute("SELECT COUNT(*) FROM shadow_decisions").fetchone() == (3,)
        stream_row = connection.execute(
            """
            SELECT latest_journal_sequence, aggregate_revision, admission_sequence
            FROM streams
            """
        ).fetchone()
        assert stream_row == (
            3,
            reserved.transition.next_state.aggregate_revision.value,
            1,
        )
    finally:
        connection.close()


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


@pytest.mark.parametrize(
    "fault_name",
    [
        "before_reduction",
        "after_reduction",
        "after_journal",
        "during_effects",
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
