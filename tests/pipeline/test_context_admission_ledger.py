"""Crash-safe context-admission ledger tests."""

from __future__ import annotations

import json
import os
import sqlite3
import stat
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path
from types import MappingProxyType

import pytest

import autoskillit.core.types._type_context_admission_persistence as persistence_types
import autoskillit.pipeline.context_admission_ledger as ledger_module
from autoskillit.core import (
    ActiveContextAdmissionState,
    AdmissionDecisionKind,
    AdmissionEventId,
    AdmissionState,
    AuthorityUnavailableEvent,
    ContextAdmissionAccountingStatus,
    ContextAdmissionStorageFailureReason,
    ContextAdmissionStorageHealthStatus,
    ContextAdmissionStoreAuthority,
    ContextAdmissionStreamKey,
    ContextThreadId,
    CoverageState,
)
from autoskillit.pipeline.context_admission_ledger import (
    DefaultContextAdmissionLedger,
)
from tests.fixtures.context_admission import (
    accept_event,
    batch,
    dispatch_event,
    event_fields,
    mark_indeterminate_event,
    occurrence,
    open_event,
    prepare_event,
    propose_event,
    reconcile_generation_event,
    release_non_admission_event,
    reserve_event,
    rollover_event,
    snapshot,
    stage_event,
    start_generation_event,
    stream_key,
    uninitialized_state,
)

pytestmark = [pytest.mark.layer("pipeline"), pytest.mark.small]

_GOLDEN_JOURNAL = (
    Path(__file__).parents[1]
    / "fixtures"
    / "context_admission_journals"
    / "protocol_v1_encoding_v1.json"
)


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


def test_recovery_removes_same_inode_crash_window_initialization_link(
    tmp_path: Path,
) -> None:
    authority = _authority(tmp_path)
    assert (
        DefaultContextAdmissionLedger(authority).recover_all().status
        is ContextAdmissionStorageHealthStatus.HEALTHY
    )
    orphan = authority.database_path.parent / (f".{authority.database_path.name}.{'a' * 24}.tmp")
    os.link(authority.database_path, orphan)
    assert authority.database_path.stat().st_nlink == 2

    result = DefaultContextAdmissionLedger(authority).recover_all()

    assert result.status is ContextAdmissionStorageHealthStatus.HEALTHY
    assert authority.database_path.stat().st_nlink == 1
    assert not orphan.exists()


def test_independent_ledgers_race_first_publication_at_shared_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    authority = _authority(tmp_path)
    ledgers = (
        DefaultContextAdmissionLedger(authority),
        DefaultContextAdmissionLedger(authority),
    )
    publication_barrier = threading.Barrier(2)
    collision_count = 0
    collision_lock = threading.Lock()
    collision_seen = threading.Event()
    original_link = ledger_module.os.link

    def racing_link(
        source: Path,
        destination: Path,
        *,
        follow_symlinks: bool = True,
    ) -> None:
        nonlocal collision_count
        publication_barrier.wait(timeout=5)
        try:
            original_link(
                source,
                destination,
                follow_symlinks=follow_symlinks,
            )
        except FileExistsError:
            with collision_lock:
                collision_count += 1
            collision_seen.set()
            raise
        assert collision_seen.wait(timeout=5)
        time.sleep(0.1)

    monkeypatch.setattr(ledger_module.os, "link", racing_link)

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = tuple(executor.map(lambda ledger: ledger.recover_all(), ledgers))

    assert collision_count == 1
    assert {result.status for result in results} == {ContextAdmissionStorageHealthStatus.HEALTHY}
    assert authority.database_path.stat().st_nlink == 1
    assert not list(authority.database_path.parent.glob("*.tmp"))


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


def test_recovery_and_inspection_hold_one_snapshot_across_projection_reads(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    authority = _authority(tmp_path)
    key = stream_key()
    assert (
        DefaultContextAdmissionLedger(authority).apply(key, open_event()).status
        is ContextAdmissionAccountingStatus.RECORDED
    )
    reader = DefaultContextAdmissionLedger(authority)
    statements: list[str] = []
    original_connect = reader._connect

    def traced_connect() -> sqlite3.Connection:
        connection = original_connect()
        connection.set_trace_callback(statements.append)
        return connection

    monkeypatch.setattr(reader, "_connect", traced_connect)

    assert reader.recover_all().status is ContextAdmissionStorageHealthStatus.HEALTHY

    begin_index = statements.index("BEGIN")
    stream_index = next(
        index
        for index, statement in enumerate(statements)
        if "FROM streams" in statement and "ORDER BY stream_id" in statement
    )
    journal_index = next(
        index
        for index, statement in enumerate(statements)
        if "FROM journal_events" in statement and "ORDER BY journal_sequence" in statement
    )
    commit_index = statements.index("COMMIT", journal_index)
    assert begin_index < stream_index < journal_index < commit_index

    inspection_start = len(statements)
    assert reader.inspect_stream(key).health.status is ContextAdmissionStorageHealthStatus.HEALTHY
    inspection_statements = statements[inspection_start:]
    assert inspection_statements[0] == "BEGIN"
    inspection_stream_index = next(
        index
        for index, statement in enumerate(inspection_statements)
        if "FROM streams WHERE stream_id" in statement
    )
    inspection_journal_index = next(
        index
        for index, statement in enumerate(inspection_statements)
        if "FROM journal_events" in statement
    )
    assert 0 < inspection_stream_index < inspection_journal_index


def test_recovery_enforces_aggregate_row_and_byte_budgets(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    authority = _authority(tmp_path)
    key = stream_key()
    assert (
        DefaultContextAdmissionLedger(authority).apply(key, open_event()).status
        is ContextAdmissionAccountingStatus.RECORDED
    )
    monkeypatch.setattr(ledger_module, "_MAX_RECOVERY_ROWS", 2)

    row_bounded = DefaultContextAdmissionLedger(authority)
    recovered = row_bounded.recover_all()

    assert recovered.status is ContextAdmissionStorageHealthStatus.HEALTHY
    health = row_bounded.stream_health(key)
    assert health.status is ContextAdmissionStorageHealthStatus.FAIL_CLOSED
    assert health.failure_reason is ContextAdmissionStorageFailureReason.INTEGRITY
    assert health.reason_code == "recovery-read-limit-exceeded"

    monkeypatch.setattr(ledger_module, "_MAX_RECOVERY_ROWS", 100_000)
    monkeypatch.setattr(ledger_module, "_MAX_RECOVERY_BYTES", 1)
    byte_bounded = DefaultContextAdmissionLedger(authority)

    byte_result = byte_bounded.recover_all()

    assert byte_result.status is ContextAdmissionStorageHealthStatus.FAIL_CLOSED
    assert (
        byte_result.store_health.failure_reason is ContextAdmissionStorageFailureReason.INTEGRITY
    )
    assert byte_result.store_health.reason_code == "recovery-read-limit-exceeded"


def test_inspection_enforces_aggregate_read_budget(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ledger = DefaultContextAdmissionLedger(_authority(tmp_path))
    key = stream_key()
    assert ledger.apply(key, open_event()).status is ContextAdmissionAccountingStatus.RECORDED
    monkeypatch.setattr(ledger_module, "_MAX_RECOVERY_ROWS", 1)

    inspection = ledger.inspect_stream(key)

    assert inspection.health.status is ContextAdmissionStorageHealthStatus.FAIL_CLOSED
    assert inspection.health.failure_reason is ContextAdmissionStorageFailureReason.INTEGRITY
    assert inspection.health.reason_code == "inspection-read-limit-exceeded"


def test_recovery_fails_closed_on_orphaned_foreign_key_rows(tmp_path: Path) -> None:
    authority = _authority(tmp_path)
    assert (
        DefaultContextAdmissionLedger(authority).recover_all().status
        is ContextAdmissionStorageHealthStatus.HEALTHY
    )
    connection = sqlite3.connect(authority.database_path)
    try:
        connection.execute("PRAGMA foreign_keys=OFF")
        connection.execute(
            """
            INSERT INTO effect_outbox(
                stream_id, journal_sequence, effect_ordinal, effect_envelope
            ) VALUES (?, 1, 0, ?)
            """,
            (b"orphan-stream", b"invalid"),
        )
        connection.commit()
    finally:
        connection.close()

    recovered = DefaultContextAdmissionLedger(authority).recover_all()

    assert recovered.status is ContextAdmissionStorageHealthStatus.FAIL_CLOSED
    assert recovered.store_health.failure_reason is ContextAdmissionStorageFailureReason.INTEGRITY
    assert recovered.store_health.reason_code == "sqlite-foreign-key-check-failed"


def test_stream_key_decoder_enforces_byte_and_nesting_bounds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    encoded = ledger_module._stream_key_bytes(stream_key())
    monkeypatch.setattr(ledger_module, "_MAX_STREAM_KEY_BYTES", len(encoded) - 1)
    with pytest.raises(RuntimeError, match="invalid-stream-key"):
        ledger_module._decode_stream_key(encoded)

    monkeypatch.setattr(ledger_module, "_MAX_STREAM_KEY_BYTES", 16 * 1024)
    deeply_nested = b'{"nested":' + (b"[" * 17) + b"null" + (b"]" * 17) + b"}"
    with pytest.raises(RuntimeError, match="invalid-stream-key"):
        ledger_module._decode_stream_key(deeply_nested)


def test_stream_key_decoder_normalizes_recursive_json_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    encoded = ledger_module._stream_key_bytes(stream_key())

    def raise_recursion(_value: str) -> object:
        raise RecursionError

    monkeypatch.setattr(ledger_module.json, "loads", raise_recursion)

    with pytest.raises(RuntimeError, match="invalid-stream-key"):
        ledger_module._decode_stream_key(encoded)


def test_store_path_uri_metacharacters_are_percent_encoded(tmp_path: Path) -> None:
    authority = ContextAdmissionStoreAuthority(
        database_path=(tmp_path / "context?admission#literal" / "ledger%literal.sqlite3"),
        expected_owner_id=os.getuid(),
    )

    result = DefaultContextAdmissionLedger(authority).recover_all()

    assert result.status is ContextAdmissionStorageHealthStatus.HEALTHY
    assert authority.database_path.is_file()


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


@pytest.mark.parametrize("target_kind", ["database", "sidecar"])
def test_existing_store_rejects_insecure_private_file_modes_without_repair(
    tmp_path: Path,
    target_kind: str,
) -> None:
    authority = _authority(tmp_path)
    assert (
        DefaultContextAdmissionLedger(authority).recover_all().status
        is ContextAdmissionStorageHealthStatus.HEALTHY
    )
    target = authority.database_path
    if target_kind == "sidecar":
        target = Path(f"{authority.database_path}-journal")
        target.write_bytes(b"untrusted")
    target.chmod(0o644)

    result = DefaultContextAdmissionLedger(authority).recover_all()

    assert result.status is ContextAdmissionStorageHealthStatus.FAIL_CLOSED
    assert (
        result.store_health.failure_reason
        is ContextAdmissionStorageFailureReason.SECURITY_IDENTITY
    )
    assert stat.S_IMODE(target.stat().st_mode) == 0o644


def test_existing_database_hard_link_fails_closed_without_unlinking(
    tmp_path: Path,
) -> None:
    authority = _authority(tmp_path)
    assert (
        DefaultContextAdmissionLedger(authority).recover_all().status
        is ContextAdmissionStorageHealthStatus.HEALTHY
    )
    hard_link = authority.database_path.with_suffix(".hard-link")
    os.link(authority.database_path, hard_link)

    result = DefaultContextAdmissionLedger(authority).recover_all()

    assert result.status is ContextAdmissionStorageHealthStatus.FAIL_CLOSED
    assert (
        result.store_health.failure_reason
        is ContextAdmissionStorageFailureReason.SECURITY_IDENTITY
    )
    assert authority.database_path.stat().st_nlink == 2
    assert hard_link.exists()


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


def test_ledger_publication_matches_protocol_v1_golden_journal(tmp_path: Path) -> None:
    fixture = json.loads(_GOLDEN_JOURNAL.read_text(encoding="utf-8"))
    authority = _authority(tmp_path)
    ledger = DefaultContextAdmissionLedger(authority)
    event = AuthorityUnavailableEvent(
        **event_fields(
            uninitialized_state(),
            "event-authority-unavailable",
            "authority-unavailable",
        ),
        reason_code="authoritative-watermark-unavailable",
        authority_state=CoverageState.PARTIAL,
    )

    recorded = ledger.apply(stream_key(), event)
    assert recorded.status is ContextAdmissionAccountingStatus.SEMANTIC_REJECTION
    assert recorded.transition is not None

    connection = sqlite3.connect(authority.database_path)
    try:
        journal = connection.execute(
            """
            SELECT journal_sequence, event_envelope, decision_envelope
            FROM journal_events ORDER BY journal_sequence
            """
        ).fetchall()
        effects = connection.execute(
            """
            SELECT journal_sequence, effect_ordinal, effect_envelope
            FROM effect_outbox ORDER BY journal_sequence, effect_ordinal
            """
        ).fetchall()
        shadows = connection.execute(
            """
            SELECT journal_sequence, shadow_envelope
            FROM shadow_decisions ORDER BY journal_sequence
            """
        ).fetchall()
        final_state = connection.execute("SELECT state_envelope FROM streams").fetchone()
    finally:
        connection.close()

    publication = fixture["publications"][0]
    assert journal == [
        (
            publication["journal_sequence"],
            publication["event_envelope"].encode("utf-8"),
            publication["decision_envelope"].encode("utf-8"),
        )
    ]
    assert effects == []
    assert shadows == [
        (
            publication["journal_sequence"],
            publication["shadow_envelope"].encode("utf-8"),
        )
    ]
    assert final_state == (fixture["final_state_envelope"].encode("utf-8"),)

    reopened = DefaultContextAdmissionLedger(authority)
    assert reopened.recover_all().status is ContextAdmissionStorageHealthStatus.HEALTHY
    assert reopened.replay(stream_key()).state == recorded.transition.next_state


def test_recovery_replays_nonempty_stream_and_surfaces_unresolved_work(
    tmp_path: Path,
) -> None:
    authority = _authority(tmp_path)
    key = stream_key()
    ledger = DefaultContextAdmissionLedger(authority)
    opened = ledger.apply(key, open_event())
    assert opened.transition is not None
    occurrence_value = occurrence()
    proposed = ledger.apply(
        key,
        propose_event(opened.transition.next_state, occurrence_value),
    )
    assert proposed.transition is not None
    reserved = ledger.reserve(
        key,
        reserve_event(
            proposed.transition.next_state,
            batch(occurrence_value),
            occurrence_value,
            generation_allowance=15,
        ),
    )
    assert reserved.transition is not None

    reopened = DefaultContextAdmissionLedger(authority)
    recovered = reopened.recover_all()

    assert recovered.status is ContextAdmissionStorageHealthStatus.HEALTHY
    assert recovered.recovered_streams == (key,)
    assert recovered.unresolved_streams == (key,)
    inspection = reopened.inspect_stream(key)
    assert inspection.state == reserved.transition.next_state
    assert inspection.latest_journal_sequence == 3
    replayed = reopened.apply(
        key,
        reserve_event(
            proposed.transition.next_state,
            batch(occurrence_value),
            occurrence_value,
            generation_allowance=15,
        ),
    )
    assert replayed.status is ContextAdmissionAccountingStatus.EXACT_REPLAY
    assert replayed.journal_sequence == 3


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


def test_recovery_remains_incomplete_when_failure_marker_is_contended(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    authority = _authority(tmp_path)
    key = stream_key()
    ledger = DefaultContextAdmissionLedger(authority)
    assert ledger.apply(key, open_event()).status is ContextAdmissionAccountingStatus.RECORDED
    connection = sqlite3.connect(authority.database_path)
    try:
        connection.execute("DELETE FROM shadow_decisions")
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
    assert not recovered._recovered


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


def test_recovery_rejects_valid_but_nonzero_genesis(tmp_path: Path) -> None:
    authority = _authority(tmp_path)
    key = stream_key()
    ledger = DefaultContextAdmissionLedger(authority)
    assert ledger.apply(key, open_event()).status is ContextAdmissionAccountingStatus.RECORDED
    connection = sqlite3.connect(authority.database_path)
    try:
        connection.execute("UPDATE streams SET genesis_envelope = state_envelope")
        connection.commit()
    finally:
        connection.close()

    recovered = DefaultContextAdmissionLedger(authority)
    result = recovered.recover_all()

    assert result.status is ContextAdmissionStorageHealthStatus.HEALTHY
    health = recovered.stream_health(key)
    assert health.status is ContextAdmissionStorageHealthStatus.FAIL_CLOSED
    assert health.failure_reason is ContextAdmissionStorageFailureReason.REPLAY_MISMATCH
    assert health.reason_code == "invalid-stream-genesis-type"


def test_recovery_rejects_journal_event_identity_mismatch(tmp_path: Path) -> None:
    authority = _authority(tmp_path)
    key = stream_key()
    assert (
        DefaultContextAdmissionLedger(authority).apply(key, open_event()).status
        is ContextAdmissionAccountingStatus.RECORDED
    )
    connection = sqlite3.connect(authority.database_path)
    try:
        connection.execute(
            "UPDATE journal_events SET event_id = ?",
            ("damaged-event-id",),
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
    assert health.reason_code == "journal-event-identity-mismatch"


def test_recovery_preflight_rejects_unsupported_encoding_without_rewrite(
    tmp_path: Path,
) -> None:
    authority = _authority(tmp_path)
    ledger = DefaultContextAdmissionLedger(authority)
    assert (
        ledger.apply(stream_key(), open_event()).status
        is ContextAdmissionAccountingStatus.RECORDED
    )
    connection = sqlite3.connect(authority.database_path)
    try:
        original = bytes(
            connection.execute("SELECT event_envelope FROM journal_events").fetchone()[0]
        )
        unsupported = original.replace(
            b'"encoding_version":1',
            b'"encoding_version":2',
            1,
        )
        connection.execute(
            "UPDATE journal_events SET event_envelope = ?",
            (unsupported,),
        )
        connection.commit()
    finally:
        connection.close()

    recovered = DefaultContextAdmissionLedger(authority)
    result = recovered.recover_all()

    assert result.status is ContextAdmissionStorageHealthStatus.FAIL_CLOSED
    assert (
        result.store_health.failure_reason
        is ContextAdmissionStorageFailureReason.UNSUPPORTED_ENCODING
    )
    connection = sqlite3.connect(authority.database_path)
    try:
        assert (
            bytes(connection.execute("SELECT event_envelope FROM journal_events").fetchone()[0])
            == unsupported
        )
    finally:
        connection.close()


@pytest.mark.parametrize(
    ("metadata_key", "invalid_value", "expected_reason", "expected_reason_code"),
    [
        (
            "schema_version",
            "2",
            ContextAdmissionStorageFailureReason.UNSUPPORTED_SCHEMA,
            "invalid-schema-version",
        ),
        (
            "encoding_version",
            "2",
            ContextAdmissionStorageFailureReason.UNSUPPORTED_ENCODING,
            "invalid-encoding-version",
        ),
        (
            "protocol_version",
            "2",
            ContextAdmissionStorageFailureReason.UNSUPPORTED_PROTOCOL,
            "invalid-protocol-version",
        ),
        (
            "store_health",
            "fail_closed",
            ContextAdmissionStorageFailureReason.INTEGRITY,
            "invalid-store-health",
        ),
    ],
)
def test_recovery_rejects_invalid_metadata_without_rewrite(
    tmp_path: Path,
    metadata_key: str,
    invalid_value: str,
    expected_reason: ContextAdmissionStorageFailureReason,
    expected_reason_code: str,
) -> None:
    authority = _authority(tmp_path)
    assert (
        DefaultContextAdmissionLedger(authority).recover_all().status
        is ContextAdmissionStorageHealthStatus.HEALTHY
    )
    connection = sqlite3.connect(authority.database_path)
    try:
        connection.execute(
            "UPDATE metadata SET value = ? WHERE key = ?",
            (invalid_value, metadata_key),
        )
        connection.commit()
    finally:
        connection.close()

    result = DefaultContextAdmissionLedger(authority).recover_all()

    assert result.status is ContextAdmissionStorageHealthStatus.FAIL_CLOSED
    assert result.store_health.failure_reason is expected_reason
    assert result.store_health.reason_code == expected_reason_code
    connection = sqlite3.connect(authority.database_path)
    try:
        stored = connection.execute(
            "SELECT value FROM metadata WHERE key = ?",
            (metadata_key,),
        ).fetchone()
        assert stored == (invalid_value,)
    finally:
        connection.close()


def test_recovery_decodes_supported_legacy_encoding_without_rewrite(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    authority = _authority(tmp_path)
    ledger = DefaultContextAdmissionLedger(authority)
    assert (
        ledger.apply(stream_key(), open_event()).status
        is ContextAdmissionAccountingStatus.RECORDED
    )
    envelope_columns = (
        ("streams", "genesis_envelope"),
        ("streams", "state_envelope"),
        ("journal_events", "event_envelope"),
        ("journal_events", "decision_envelope"),
        ("effect_outbox", "effect_envelope"),
        ("shadow_decisions", "shadow_envelope"),
    )
    connection = sqlite3.connect(authority.database_path)
    try:
        for table, column in envelope_columns:
            for row_id, value in connection.execute(f"SELECT rowid, {column} FROM {table}"):
                legacy = bytes(value).replace(
                    b'"encoding_version":1',
                    b'"encoding_version":0',
                    1,
                )
                connection.execute(
                    f"UPDATE {table} SET {column} = ? WHERE rowid = ?",
                    (legacy, row_id),
                )
        connection.commit()
    finally:
        connection.close()
    monkeypatch.setattr(
        persistence_types,
        "CONTEXT_ADMISSION_ENVELOPE_UPCASTERS",
        MappingProxyType(
            {
                (0, 1): lambda value: value.replace(
                    b'"encoding_version":0',
                    b'"encoding_version":1',
                    1,
                )
            }
        ),
    )

    result = DefaultContextAdmissionLedger(authority).recover_all()

    assert result.status is ContextAdmissionStorageHealthStatus.HEALTHY
    connection = sqlite3.connect(authority.database_path)
    try:
        stored = bytes(
            connection.execute("SELECT event_envelope FROM journal_events").fetchone()[0]
        )
        assert b'"encoding_version":0' in stored
    finally:
        connection.close()


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
            generation_allowance=15,
        ),
    )
    assert reserved.status is ContextAdmissionAccountingStatus.RECORDED
    assert reserved.journal_sequence == 3
    assert reserved.transition is not None
    assert reserved.transition.next_state.admission_sequence.value == 1
    inspection = ledger.inspect_stream(key)
    assert inspection.events[-1].event_id.value == "event-reserve"
    assert inspection.effects[-1] == reserved.transition.effects
    assert inspection.state == reserved.transition.next_state
    input_target, generation_target = inspection.shadows[-1].targets
    assert input_target.proposed_input_count == 10
    assert input_target.generation_allowance is None
    assert input_target.producer_surfaces == (occurrence_value.lineage.producer_surface,)
    assert input_target.turn_ids == (occurrence_value.lineage.turn_id,)
    assert generation_target.proposed_input_count is None
    assert generation_target.generation_allowance == 15
    assert generation_target.exact_input_charge is None
    assert generation_target.exact_output_charge is None

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


def test_shadow_projection_preserves_exact_input_and_output_measurements(
    tmp_path: Path,
) -> None:
    ledger = DefaultContextAdmissionLedger(_authority(tmp_path))
    key = stream_key()
    occurrence_value = occurrence()
    batch_value = batch(occurrence_value)

    opened = ledger.apply(key, open_event())
    assert opened.transition is not None
    proposed = ledger.apply(
        key,
        propose_event(opened.transition.next_state, occurrence_value),
    )
    assert proposed.transition is not None
    reserved = ledger.reserve(
        key,
        reserve_event(
            proposed.transition.next_state,
            batch_value,
            occurrence_value,
            generation_allowance=15,
        ),
    )
    assert reserved.transition is not None
    prepared = ledger.apply(
        key,
        prepare_event(reserved.transition.next_state, batch_value),
    )
    assert prepared.transition is not None
    staged = ledger.apply(
        key,
        stage_event(prepared.transition.next_state, batch_value),
    )
    assert staged.transition is not None
    dispatched = ledger.apply(
        key,
        dispatch_event(staged.transition.next_state, batch_value),
    )
    assert dispatched.transition is not None
    generation_started = ledger.apply(
        key,
        start_generation_event(dispatched.transition.next_state, batch_value),
    )
    assert generation_started.transition is not None
    accepted = ledger.commit(
        key,
        accept_event(
            generation_started.transition.next_state,
            batch_value,
            exact_input_charge=9,
        ),
    )
    assert accepted.transition is not None
    reconciled = ledger.commit(
        key,
        reconcile_generation_event(
            accepted.transition.next_state,
            batch_value,
            exact_output_usage=7,
        ),
    )
    assert reconciled.transition is not None

    inspection = ledger.replay(key)
    assert inspection.latest_journal_sequence == 9
    assert tuple(record.journal_sequence for record in inspection.shadows) == tuple(range(1, 10))
    accepted_target = inspection.shadows[7].targets[0]
    assert accepted_target.exact_input_charge == 9
    assert accepted_target.measurement_kind is not None
    reconciled_target = inspection.shadows[8].targets[0]
    assert reconciled_target.exact_output_charge == 7
    assert reconciled_target.generation_allowance == 15


def test_rollover_projection_retains_every_invalidated_target_and_replays(
    tmp_path: Path,
) -> None:
    authority = _authority(tmp_path)
    ledger = DefaultContextAdmissionLedger(authority)
    key = stream_key()
    occurrence_value = occurrence()
    batch_value = batch(occurrence_value)
    opened = ledger.apply(key, open_event())
    assert opened.transition is not None
    proposed = ledger.apply(
        key,
        propose_event(opened.transition.next_state, occurrence_value),
    )
    assert proposed.transition is not None
    reserved = ledger.reserve(
        key,
        reserve_event(
            proposed.transition.next_state,
            batch_value,
            occurrence_value,
            generation_allowance=15,
        ),
    )
    assert reserved.transition is not None
    rolled_over = ledger.apply(
        key,
        rollover_event(reserved.transition.next_state, batch_value),
    )
    assert rolled_over.transition is not None

    targets = ledger.inspect_stream(key).shadows[-1].targets
    assert tuple(target.target_id.value for target in targets) == (
        "batch-one",
        "generation-one",
    )
    assert targets[0].proposed_input_count == 10
    assert targets[1].generation_allowance == 15
    recovered = DefaultContextAdmissionLedger(authority).recover_all()
    assert recovered.status is ContextAdmissionStorageHealthStatus.HEALTHY
    assert recovered.unresolved_streams == ()


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


def test_reservation_key_retry_appends_one_noop_then_exact_replays(
    tmp_path: Path,
) -> None:
    authority = _authority(tmp_path)
    ledger = DefaultContextAdmissionLedger(authority)
    key = stream_key()
    occurrence_value = occurrence()
    opened = ledger.apply(key, open_event())
    assert opened.transition is not None
    proposed = ledger.apply(
        key,
        propose_event(opened.transition.next_state, occurrence_value),
    )
    assert proposed.transition is not None
    original_event = reserve_event(
        proposed.transition.next_state,
        batch(occurrence_value),
        occurrence_value,
    )
    reserved = ledger.reserve(key, original_event)
    assert reserved.transition is not None

    retry_event = replace(
        original_event,
        event_id=AdmissionEventId("event-reserve-new-event-id"),
        expected_aggregate_revision=reserved.transition.next_state.aggregate_revision,
    )
    noop = ledger.reserve(key, retry_event)

    assert noop.status is ContextAdmissionAccountingStatus.RECORDED
    assert noop.transition is not None
    assert noop.transition.decision.kind is AdmissionDecisionKind.NOOP_IDEMPOTENT
    assert noop.transition.effects == ()
    assert noop.journal_sequence == 4
    exact = ledger.reserve(key, retry_event)
    assert exact.status is ContextAdmissionAccountingStatus.EXACT_REPLAY
    assert exact.journal_sequence == 4

    inspection = ledger.inspect_stream(key)
    assert inspection.latest_journal_sequence == 4
    assert inspection.state == noop.transition.next_state


def test_unretained_reservation_conflict_exact_replays_stored_decision(
    tmp_path: Path,
) -> None:
    authority = _authority(tmp_path)
    ledger = DefaultContextAdmissionLedger(authority)
    key = stream_key()
    occurrence_value = occurrence()
    opened = ledger.apply(key, open_event())
    assert opened.transition is not None
    proposed = ledger.apply(
        key,
        propose_event(opened.transition.next_state, occurrence_value),
    )
    assert proposed.transition is not None
    original_event = reserve_event(
        proposed.transition.next_state,
        batch(occurrence_value),
        occurrence_value,
    )
    reserved = ledger.reserve(key, original_event)
    assert reserved.transition is not None

    changed_reservation = replace(
        original_event.input_reservations[0],
        reserved_count=original_event.input_reservations[0].reserved_count + 1,
    )
    conflict_event = replace(
        original_event,
        event_id=AdmissionEventId("event-reserve-conflict"),
        expected_aggregate_revision=reserved.transition.next_state.aggregate_revision,
        input_reservations=(changed_reservation,),
    )
    conflict = ledger.reserve(key, conflict_event)

    assert conflict.status is ContextAdmissionAccountingStatus.SEMANTIC_REJECTION
    assert conflict.transition is not None
    assert conflict.transition.decision.kind is AdmissionDecisionKind.CONFLICT
    assert conflict.journal_sequence == 4
    replayed = ledger.reserve(key, conflict_event)
    assert replayed.status is ContextAdmissionAccountingStatus.EXACT_REPLAY
    assert replayed.journal_sequence == 4
    assert replayed.transition is not None
    assert replayed.transition.decision == conflict.transition.decision
    assert replayed.transition.effects == ()


def test_explicit_non_admission_witness_releases_reserved_capacity(
    tmp_path: Path,
) -> None:
    ledger = DefaultContextAdmissionLedger(_authority(tmp_path))
    key = stream_key()
    occurrence_value = occurrence()
    batch_value = batch(occurrence_value)
    opened = ledger.apply(key, open_event())
    assert opened.transition is not None
    proposed = ledger.apply(
        key,
        propose_event(opened.transition.next_state, occurrence_value),
    )
    assert proposed.transition is not None
    reserved = ledger.reserve(
        key,
        reserve_event(proposed.transition.next_state, batch_value, occurrence_value),
    )
    assert reserved.transition is not None

    released = ledger.release(
        key,
        release_non_admission_event(reserved.transition.next_state, batch_value),
    )

    assert released.status is ContextAdmissionAccountingStatus.RECORDED
    assert released.transition is not None
    batch_record = released.transition.next_state.batch_records[0]
    assert batch_record.state is AdmissionState.RELEASED
    assert released.transition.decision.available_ordinary_count == 40


def test_dispatched_indeterminate_work_remains_charged_across_recovery(
    tmp_path: Path,
) -> None:
    authority = _authority(tmp_path)
    ledger = DefaultContextAdmissionLedger(authority)
    key = stream_key()
    occurrence_value = occurrence()
    batch_value = batch(occurrence_value)
    opened = ledger.apply(key, open_event())
    assert opened.transition is not None
    proposed = ledger.apply(
        key,
        propose_event(opened.transition.next_state, occurrence_value),
    )
    assert proposed.transition is not None
    reserved = ledger.reserve(
        key,
        reserve_event(proposed.transition.next_state, batch_value, occurrence_value),
    )
    assert reserved.transition is not None
    prepared = ledger.apply(
        key,
        prepare_event(reserved.transition.next_state, batch_value),
    )
    assert prepared.transition is not None
    staged = ledger.apply(
        key,
        stage_event(prepared.transition.next_state, batch_value),
    )
    assert staged.transition is not None
    dispatched = ledger.apply(
        key,
        dispatch_event(staged.transition.next_state, batch_value),
    )
    assert dispatched.transition is not None
    marked = ledger.apply(
        key,
        mark_indeterminate_event(dispatched.transition.next_state, batch_value),
    )
    assert marked.status is ContextAdmissionAccountingStatus.RECONCILIATION_REQUIRED
    assert marked.transition is not None

    reopened = DefaultContextAdmissionLedger(authority)
    recovered = reopened.recover_all()
    state = reopened.inspect_stream(key).state

    assert recovered.unresolved_streams == (key,)
    assert isinstance(state, ActiveContextAdmissionState)
    assert state.batch_records[0].state is AdmissionState.INDETERMINATE
    assert sum(reservation.reserved_count for reservation in state.reservations) == 10


def test_rejected_indeterminate_event_is_reported_as_semantic_rejection(
    tmp_path: Path,
) -> None:
    ledger = DefaultContextAdmissionLedger(_authority(tmp_path))
    key = stream_key()
    opened = ledger.apply(key, open_event())
    assert opened.transition is not None
    batch_value = batch(occurrence())

    rejected = ledger.apply(
        key,
        mark_indeterminate_event(opened.transition.next_state, batch_value),
    )

    assert rejected.status is ContextAdmissionAccountingStatus.SEMANTIC_REJECTION
    assert rejected.transition is not None
    assert rejected.transition.decision.kind is AdmissionDecisionKind.WOULD_REJECT


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
