"""Crash-safe context-admission ledger tests."""

from __future__ import annotations

import os
import sqlite3
import stat
from pathlib import Path

import pytest

from autoskillit.core import (
    ContextAdmissionStorageFailureReason,
    ContextAdmissionStorageHealthStatus,
    ContextAdmissionStoreAuthority,
)
from autoskillit.pipeline.context_admission_ledger import (
    DefaultContextAdmissionLedger,
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
