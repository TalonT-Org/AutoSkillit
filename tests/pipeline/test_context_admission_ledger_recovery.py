"""Recovery tests for the crash-safe context-admission ledger.

Part of the test split for issue #4606.
"""

from __future__ import annotations

import os
import sqlite3
import stat
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path
from types import MappingProxyType
from typing import cast
from unittest.mock import MagicMock

import pytest

import autoskillit.pipeline._context_admission_storage as storage_module
import autoskillit.pipeline.context_admission_ledger as ledger_module
from autoskillit.core import (
    ContextAdmissionAccountingStatus,
    ContextAdmissionStorageFailureReason,
    ContextAdmissionStorageHealthStatus,
    ContextAdmissionStoreAuthority,
    ContextAdmissionStreamHealth,
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
    stream_key,
)
from tests.pipeline._context_admission_ledger_helpers import (
    _authority,
)

pytestmark = [pytest.mark.layer("pipeline"), pytest.mark.small]


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


def test_connection_factory_pragma_readback_mismatch_fails_closed(
    tmp_path: Path,
) -> None:
    authority = _authority(tmp_path)

    def mismatched_factory(*args: object, **kwargs: object) -> sqlite3.Connection:
        connection = sqlite3.connect(*args, **kwargs)  # type: ignore[arg-type]
        proxy = MagicMock(wraps=connection, spec=sqlite3.Connection)

        def execute(sql: str, *parameters: object) -> sqlite3.Cursor:
            if sql == "PRAGMA foreign_keys=ON":
                return connection.execute("SELECT 1 WHERE 0")
            return connection.execute(sql, *parameters)  # type: ignore[arg-type]

        proxy.execute.side_effect = execute
        return cast(sqlite3.Connection, proxy)

    result = DefaultContextAdmissionLedger(
        authority,
        connection_factory=mismatched_factory,
    ).recover_all()

    assert result.status is ContextAdmissionStorageHealthStatus.FAIL_CLOSED
    assert result.store_health.failure_reason is ContextAdmissionStorageFailureReason.CONFIGURATION
    assert result.store_health.reason_code == "sqlite-pragma-mismatch"


def test_connection_factory_identity_change_fails_closed(
    tmp_path: Path,
) -> None:
    authority = _authority(tmp_path)
    assert (
        DefaultContextAdmissionLedger(authority).recover_all().status
        is ContextAdmissionStorageHealthStatus.HEALTHY
    )

    def replacing_factory(*args: object, **kwargs: object) -> sqlite3.Connection:
        connection = sqlite3.connect(*args, **kwargs)  # type: ignore[arg-type]
        replacement_path = authority.database_path.with_suffix(".replacement")
        replacement = sqlite3.connect(replacement_path)
        try:
            connection.backup(replacement)
        finally:
            replacement.close()
        replacement_path.chmod(0o600)
        os.replace(replacement_path, authority.database_path)
        return connection

    result = DefaultContextAdmissionLedger(
        authority,
        connection_factory=replacing_factory,
    ).recover_all()

    assert result.status is ContextAdmissionStorageHealthStatus.FAIL_CLOSED
    assert (
        result.store_health.failure_reason
        is ContextAdmissionStorageFailureReason.SECURITY_IDENTITY
    )
    assert result.store_health.reason_code == "store-identity-changed"


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


@pytest.mark.parametrize("writable_mode", [stat.S_IWGRP, stat.S_IWOTH])
def test_writable_trusted_parent_fails_closed(
    tmp_path: Path,
    writable_mode: int,
) -> None:
    authority = _authority(tmp_path)
    original_mode = stat.S_IMODE(tmp_path.stat().st_mode)
    tmp_path.chmod(original_mode | writable_mode)
    try:
        result = DefaultContextAdmissionLedger(authority).recover_all()
    finally:
        tmp_path.chmod(original_mode)

    assert result.status is ContextAdmissionStorageHealthStatus.FAIL_CLOSED
    assert (
        result.store_health.failure_reason
        is ContextAdmissionStorageFailureReason.SECURITY_IDENTITY
    )
    assert result.store_health.reason_code == "untrusted-store-parent"
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


def test_existing_store_normalizes_sidecar_metadata_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    authority = _authority(tmp_path)
    assert (
        DefaultContextAdmissionLedger(authority).recover_all().status
        is ContextAdmissionStorageHealthStatus.HEALTHY
    )
    original_lstat = Path.lstat

    def fail_sidecar(path: Path) -> os.stat_result:
        if str(path).endswith("-journal"):
            raise OSError("sidecar metadata unavailable")
        return original_lstat(path)

    monkeypatch.setattr(Path, "lstat", fail_sidecar)

    result = DefaultContextAdmissionLedger(authority).recover_all()

    assert result.status is ContextAdmissionStorageHealthStatus.FAIL_CLOSED
    assert result.store_health.failure_reason is ContextAdmissionStorageFailureReason.IO
    assert result.store_health.reason_code == "store-sidecar-unavailable"


def test_existing_store_tolerates_sidecar_disappearing_during_identity_check(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    authority = _authority(tmp_path)
    assert (
        DefaultContextAdmissionLedger(authority).recover_all().status
        is ContextAdmissionStorageHealthStatus.HEALTHY
    )
    sidecar = Path(f"{authority.database_path}-journal")
    sidecar.write_bytes(b"transient")
    sidecar.chmod(0o600)
    original_identity = storage_module.private_file_identity
    vanished = False

    def vanish_during_identity(
        path: Path,
        *,
        owner_id: int,
        file_mode: int,
    ) -> tuple[int, int] | None:
        nonlocal vanished
        if path == sidecar and not vanished:
            vanished = True
            path.unlink()
            raise FileNotFoundError(path)
        return original_identity(path, owner_id=owner_id, file_mode=file_mode)

    monkeypatch.setattr(
        storage_module,
        "private_file_identity",
        vanish_during_identity,
    )

    result = DefaultContextAdmissionLedger(authority).recover_all()

    assert vanished
    assert result.status is ContextAdmissionStorageHealthStatus.HEALTHY


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
    assert ledger.recover_all().unresolved_streams == (key,)

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


def test_recovery_uses_registered_stream_replay(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    authority = _authority(tmp_path)
    key = stream_key()
    assert (
        DefaultContextAdmissionLedger(authority).apply(key, open_event()).status
        is ContextAdmissionAccountingStatus.RECORDED
    )
    original_selector = ledger_module.context_admission_reducer_for_protocol
    replay_calls = 0

    def select_reducer(protocol_version: int) -> object:
        reducer = original_selector(protocol_version)

        def replay_stream(initial_state: object, events: object) -> object:
            nonlocal replay_calls
            replay_calls += 1
            return reducer.replay_stream(initial_state, events)  # type: ignore[arg-type]

        return replace(reducer, replay_stream=replay_stream)

    monkeypatch.setattr(
        ledger_module,
        "context_admission_reducer_for_protocol",
        select_reducer,
    )

    recovered = DefaultContextAdmissionLedger(authority).recover_all()

    assert recovered.recovered_streams == (key,)
    assert replay_calls == 1


def test_recovery_uses_versioned_shadow_projector(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    authority = _authority(tmp_path)
    key = stream_key()
    assert (
        DefaultContextAdmissionLedger(authority).apply(key, open_event()).status
        is ContextAdmissionAccountingStatus.RECORDED
    )
    projector = MagicMock(side_effect=ledger_module._shadow_record_protocol_v1)
    registry = MappingProxyType({1: projector})
    monkeypatch.setattr(
        ledger_module,
        "_CONTEXT_ADMISSION_SHADOW_PROJECTORS",
        registry,
    )

    recovered = DefaultContextAdmissionLedger(authority).recover_all()

    assert recovered.recovered_streams == (key,)
    projector.assert_called_once()
    assert tuple(registry) == (1,)
    assert registry.keys() == ledger_module.CONTEXT_ADMISSION_REDUCER_REGISTRY.keys()
    with pytest.raises(TypeError, match="does not support item assignment"):
        registry[2] = projector  # type: ignore[index]


def test_recover_filters_healthy_failed_unresolved_unknown_and_store_failure(
    tmp_path: Path,
) -> None:
    authority = _authority(tmp_path)
    unresolved_key = stream_key()
    healthy_key = replace(
        unresolved_key,
        current_thread_id=ContextThreadId("thread-healthy"),
    )
    failed_key = replace(
        unresolved_key,
        current_thread_id=ContextThreadId("thread-failed"),
    )
    unknown_key = replace(
        unresolved_key,
        current_thread_id=ContextThreadId("thread-unknown"),
    )
    ledger = DefaultContextAdmissionLedger(authority)
    assert (
        ledger.apply(healthy_key, open_event()).status is ContextAdmissionAccountingStatus.RECORDED
    )
    assert (
        ledger.apply(failed_key, open_event()).status is ContextAdmissionAccountingStatus.RECORDED
    )
    opened = ledger.apply(unresolved_key, open_event())
    assert opened.transition is not None
    occurrence_value = occurrence()
    proposed = ledger.apply(
        unresolved_key,
        propose_event(opened.transition.next_state, occurrence_value),
    )
    assert proposed.transition is not None
    reserved = ledger.reserve(
        unresolved_key,
        reserve_event(
            proposed.transition.next_state,
            batch(occurrence_value),
            occurrence_value,
        ),
    )
    assert reserved.status is ContextAdmissionAccountingStatus.RECORDED
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

    healthy = recovered.recover(healthy_key)
    assert healthy.recovered_streams == (healthy_key,)
    assert healthy.unresolved_streams == ()
    assert healthy.stream_healths[0].status is ContextAdmissionStorageHealthStatus.HEALTHY

    unresolved = recovered.recover(unresolved_key)
    assert unresolved.recovered_streams == (unresolved_key,)
    assert unresolved.unresolved_streams == (unresolved_key,)

    failed = recovered.recover(failed_key)
    assert failed.recovered_streams == ()
    assert failed.unresolved_streams == ()
    assert failed.stream_healths[0].status is ContextAdmissionStorageHealthStatus.FAIL_CLOSED

    unknown = recovered.recover(unknown_key)
    assert unknown.stream_healths == ()
    assert unknown.recovered_streams == ()
    assert unknown.unresolved_streams == ()

    failed_authority = ContextAdmissionStoreAuthority(
        database_path=tmp_path / "failed-store" / "ledger.sqlite3",
        expected_owner_id=os.getuid(),
    )
    failed_authority.database_path.parent.mkdir(mode=0o755)
    failed_authority.database_path.parent.chmod(0o755)
    store_failed = DefaultContextAdmissionLedger(failed_authority).recover(unknown_key)
    assert store_failed.status is ContextAdmissionStorageHealthStatus.FAIL_CLOSED
    assert store_failed.stream_healths == ()


def test_sqlite_recovery_preserves_failure_discovered_during_inspection(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    authority = _authority(tmp_path)
    key = stream_key()
    event = open_event()
    ledger = DefaultContextAdmissionLedger(authority)
    assert ledger.apply(key, event).status is ContextAdmissionAccountingStatus.RECORDED
    failure = ContextAdmissionStreamHealth(
        key,
        ContextAdmissionStorageHealthStatus.FAIL_CLOSED,
        failure_reason=ContextAdmissionStorageFailureReason.REPLAY_MISMATCH,
        reason_code="inspection-publication-mismatch",
    )
    monkeypatch.setattr(
        ledger,
        "inspect_stream",
        lambda _key: ledger_module._empty_inspection(key, failure),
    )
    connection = ledger._connect()

    recovered = ledger._recover_sqlite_result(connection, key, event)

    assert recovered.status is ContextAdmissionAccountingStatus.STORAGE_FAIL_CLOSED
    assert recovered.failure_reason is ContextAdmissionStorageFailureReason.REPLAY_MISMATCH
    assert recovered.reason_code == "inspection-publication-mismatch"
    assert ledger.store_health().status is ContextAdmissionStorageHealthStatus.HEALTHY


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
