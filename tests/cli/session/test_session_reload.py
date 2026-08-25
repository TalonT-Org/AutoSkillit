"""Unit tests for consume_reload_sentinel: lock-serialized concurrent access
and funnel-routed mtime observation.

Complements tests/cli/test_reload_loop.py (RL-5), which exercises this
function only transitively (through _run_interactive_session, a single
sentinel, no concurrency). This module unit-tests consume_reload_sentinel
directly against the real filesystem.
"""

from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path
from unittest.mock import Mock

import pytest

from autoskillit.cli.session._session_reload import consume_reload_sentinel

pytestmark = [pytest.mark.layer("cli"), pytest.mark.small]


def _write_sentinel(sentinel_dir: Path, session_id: str, *, mtime: float | None = None) -> Path:
    sentinel_dir.mkdir(parents=True, exist_ok=True)
    sentinel = sentinel_dir / f"{session_id}.json"
    sentinel.write_text(json.dumps({"session_id": session_id}), encoding="utf-8")
    if mtime is not None:
        os.utime(sentinel, (mtime, mtime))
    return sentinel


def test_consume_reload_sentinel_survives_concurrent_deletion_during_stat(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A sentinel deleted between glob() and its mtime observation must not crash
    consume_reload_sentinel — the funnel must swallow the vanish, not propagate it."""
    sentinel_dir = tmp_path / ".autoskillit" / "temp" / "reload_sentinel"
    now = time.time()
    older = _write_sentinel(sentinel_dir, "older-session", mtime=now - 10)
    _write_sentinel(sentinel_dir, "newer-session", mtime=now)

    real_stat = os.stat
    deleted = {"done": False}

    def fake_stat(
        path: int | str | os.PathLike[str],
        *,
        dir_fd: int | None = None,
        follow_symlinks: bool = True,
    ) -> os.stat_result:
        if not deleted["done"] and isinstance(path, (str, os.PathLike)) and Path(path) == older:
            deleted["done"] = True
            older.unlink(missing_ok=True)
        return real_stat(path, dir_fd=dir_fd, follow_symlinks=follow_symlinks)

    monkeypatch.setattr(os, "stat", fake_stat)

    result = consume_reload_sentinel(tmp_path)

    assert result == "newer-session"


def test_consume_reload_sentinel_prunes_stale_candidates(tmp_path: Path) -> None:
    """candidates[1:] (the stale-prune loop) has zero coverage today — RL-5 only
    ever seeds one file. Pin that all older candidates are pruned, not just that
    the winner is returned."""
    sentinel_dir = tmp_path / ".autoskillit" / "temp" / "reload_sentinel"
    now = time.time()
    for i, label in enumerate(("oldest", "middle", "newest")):
        _write_sentinel(sentinel_dir, f"{label}-session", mtime=now + i)

    result = consume_reload_sentinel(tmp_path)

    assert result == "newest-session"
    assert list(sentinel_dir.glob("*.json")) == []


@pytest.mark.parametrize("failed_session_id", ["older-session", "newer-session"])
def test_consume_reload_sentinel_reports_cleanup_failure_without_consuming(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failed_session_id: str
) -> None:
    sentinel_dir = tmp_path / ".autoskillit" / "temp" / "reload_sentinel"
    now = time.time()
    older = _write_sentinel(sentinel_dir, "older-session", mtime=now - 1)
    newer = _write_sentinel(sentinel_dir, "newer-session", mtime=now)
    failed_path = {older.stem: older, newer.stem: newer}[failed_session_id]
    real_unlink = Path.unlink

    def fail_selected_unlink(path: Path, missing_ok: bool = False) -> None:
        if path == failed_path:
            raise PermissionError(f"cannot unlink {path.name}")
        real_unlink(path, missing_ok=missing_ok)

    logger = Mock()
    monkeypatch.setattr(Path, "unlink", fail_selected_unlink)
    monkeypatch.setattr("autoskillit.cli.session._session_reload.logger", logger)

    assert consume_reload_sentinel(tmp_path) is None
    assert failed_path.exists()
    logger.warning.assert_called_once_with(
        "reload_sentinel_cleanup_failed", path=str(failed_path), exc_info=True
    )


@pytest.mark.parametrize("payload", [None, 42, "session", ["session"]])
def test_consume_reload_sentinel_rejects_non_object_json(tmp_path: Path, payload: object) -> None:
    sentinel_dir = tmp_path / ".autoskillit" / "temp" / "reload_sentinel"
    sentinel_dir.mkdir(parents=True)
    (sentinel_dir / "invalid.json").write_text(json.dumps(payload), encoding="utf-8")

    assert consume_reload_sentinel(tmp_path) is None


def test_consume_reload_sentinel_serializes_concurrent_callers(tmp_path: Path) -> None:
    """N threads calling consume_reload_sentinel concurrently against a shared
    directory must never crash or double-consume the same sentinel — the
    directory lock must fully serialize the enumerate/prune/read/delete
    sequence across callers. Since a single call consumes every candidate in
    the directory (prunes all-but-newest, then also deletes the winner), a
    correctly serialized round always yields exactly one non-None winner —
    every other concurrent caller sees an already-drained directory.
    """
    sentinel_dir = tmp_path / ".autoskillit" / "temp" / "reload_sentinel"
    n_threads = 8
    n_rounds = 5

    for round_idx in range(n_rounds):
        if sentinel_dir.is_dir():
            for stray in sentinel_dir.glob("*.json"):
                stray.unlink(missing_ok=True)
        expected_ids = {
            _write_sentinel(sentinel_dir, f"round{round_idx}-session{i}").stem
            for i in range(n_threads)
        }

        barrier = threading.Barrier(n_threads)
        results: list[str | None] = [None] * n_threads
        errors: list[BaseException] = []
        errors_lock = threading.Lock()

        def worker(idx: int) -> None:
            try:
                barrier.wait(timeout=10)
                results[idx] = consume_reload_sentinel(tmp_path)
            except BaseException as exc:  # noqa: BLE001 - captured for the assertion below
                with errors_lock:
                    errors.append(exc)

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(n_threads)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors, f"round {round_idx}: thread(s) raised: {errors!r}"
        returned = [r for r in results if r is not None]
        assert len(returned) == 1, (
            f"round {round_idx}: expected exactly one winner among {n_threads} "
            f"concurrent callers, got {returned!r}"
        )
        assert returned[0] in expected_ids
