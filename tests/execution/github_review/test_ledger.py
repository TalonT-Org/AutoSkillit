"""SQLite durability, privacy, and identity contracts for review publication."""

from __future__ import annotations

import hashlib
import os
import sqlite3
import stat
from pathlib import Path

import pytest

from autoskillit.core import ReviewOperationState
from autoskillit.execution import GitHubReviewLedger

pytestmark = [pytest.mark.layer("execution"), pytest.mark.small]


def _payload(marker: str = "a") -> bytes:
    return (
        b'{"repository":"octo/example","pr_number":42,"head_sha":"'
        + marker.encode("ascii") * 40
        + b'"}'
    )


def _digest(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _prepare(
    ledger: GitHubReviewLedger,
    *,
    payload: bytes | None = None,
    operation_key: str | None = None,
) -> object:
    canonical = payload or _payload()
    return ledger.prepare(
        operation_key=operation_key or _digest(canonical),
        request_digest=_digest(canonical),
        request_json=canonical,
    )


def test_initialize_creates_strict_private_schema(tmp_path: Path) -> None:
    database_path = tmp_path / "state" / "github-review" / "ledger.sqlite3"
    ledger = GitHubReviewLedger(database_path)
    ledger.initialize()

    assert stat.S_IMODE(database_path.parent.stat().st_mode) == 0o700
    assert stat.S_IMODE(database_path.stat().st_mode) == 0o600
    for sibling in database_path.parent.iterdir():
        if sibling.is_file():
            assert stat.S_IMODE(sibling.stat().st_mode) == 0o600

    with sqlite3.connect(database_path) as connection:
        schema = {
            name: sql
            for name, sql in connection.execute(
                "SELECT name, sql FROM sqlite_schema WHERE type = 'table'"
            )
            if not name.startswith("sqlite_")
        }
        assert {
            "metadata",
            "operations",
            "operation_findings",
            "attempts",
            "receipts",
            "rate_scopes",
        } <= set(schema)
        for table_name in (
            "metadata",
            "operations",
            "operation_findings",
            "attempts",
            "receipts",
            "rate_scopes",
        ):
            assert "STRICT" in schema[table_name].upper()
        assert connection.execute("PRAGMA journal_mode").fetchone()[0] == "delete"
        assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    with ledger._connect() as connection:
        assert connection.execute("PRAGMA synchronous").fetchone()[0] == 3


def test_prepare_is_idempotent_across_restart(tmp_path: Path) -> None:
    database_path = tmp_path / "ledger.sqlite3"
    first = GitHubReviewLedger(database_path)
    second = GitHubReviewLedger(database_path)
    payload = _payload()
    operation_key = _digest(payload)

    assert _prepare(first, payload=payload) is ReviewOperationState.PREPARED
    assert _prepare(second, payload=payload) is ReviewOperationState.PREPARED
    assert second.load_receipt(operation_key) is None

    with sqlite3.connect(database_path) as connection:
        count = connection.execute(
            "SELECT COUNT(*) FROM operations WHERE operation_key = ?",
            (operation_key,),
        ).fetchone()[0]
    assert count == 1


def test_same_operation_key_cannot_be_rebound_to_different_request(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "ledger.sqlite3"
    ledger = GitHubReviewLedger(database_path)
    original = _payload("a")
    operation_key = _digest(original)
    _prepare(ledger, payload=original, operation_key=operation_key)

    with pytest.raises(ValueError, match="identity"):
        _prepare(ledger, payload=_payload("b"), operation_key=operation_key)

    with sqlite3.connect(database_path) as connection:
        row = connection.execute(
            "SELECT request_digest, request_json FROM operations WHERE operation_key = ?",
            (operation_key,),
        ).fetchone()
    assert row == (_digest(original), original)


def test_initialize_refuses_database_symlink(tmp_path: Path) -> None:
    target = tmp_path / "attacker.sqlite3"
    target.write_bytes(b"do-not-touch")
    database_path = tmp_path / "state" / "ledger.sqlite3"
    database_path.parent.mkdir()
    database_path.symlink_to(target)

    with pytest.raises((OSError, RuntimeError, ValueError), match="symlink|identity|unsafe"):
        GitHubReviewLedger(database_path).initialize()
    assert target.read_bytes() == b"do-not-touch"


def test_initialize_rejects_insecure_modes_on_owned_regular_paths(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "state" / "ledger.sqlite3"
    ledger = GitHubReviewLedger(database_path)
    ledger.initialize()
    os.chmod(database_path.parent, 0o755)
    os.chmod(database_path, 0o644)

    with pytest.raises(ValueError, match="unsafe|identity"):
        GitHubReviewLedger(database_path).initialize()
