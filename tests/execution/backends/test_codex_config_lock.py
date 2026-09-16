"""Codex shared-configuration locking contracts."""

from __future__ import annotations

import multiprocessing
from pathlib import Path

import pytest

from autoskillit.execution.backends._codex_config_lock import CodexConfigLock

pytestmark = [pytest.mark.layer("execution"), pytest.mark.medium]


def _hold_config_lock(lock_path: str, ready: object, release: object) -> None:
    lock = CodexConfigLock(Path(lock_path), timeout=5.0)
    try:
        lock.acquire()
        ready_event = ready
        release_event = release
        ready_event.set()  # type: ignore[attr-defined]
        if not release_event.wait(10):  # type: ignore[attr-defined]
            raise RuntimeError("timed out waiting to release Codex config lock")
    finally:
        lock.release()


def test_codex_config_lock_fail_fast_timeout_reports_owner_diagnostics(tmp_path: Path) -> None:
    config_path = tmp_path / "codex" / "config.toml"
    context = multiprocessing.get_context("spawn")
    ready = context.Event()
    release = context.Event()
    holder = context.Process(
        target=_hold_config_lock,
        args=(str(config_path), ready, release),
    )
    holder.start()
    try:
        assert ready.wait(5)

        with pytest.raises(TimeoutError) as captured:
            CodexConfigLock(config_path, timeout=0.0).acquire()

        message = str(captured.value)
        assert "after 0.000s" in message
        assert f"lock_path={config_path.parent / '.config.toml.autoskillit.lock'}" in message
        assert 'owner={"acquired_at_unix":' in message
        assert '"pid":' in message
    finally:
        release.set()
        holder.join(5)
        if holder.is_alive():
            holder.terminate()
            holder.join(5)

    assert holder.exitcode == 0


def test_codex_config_lock_diagnostic_failure_releases_acquired_lock(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_path = tmp_path / "codex" / "config.toml"
    failed_lock = CodexConfigLock(config_path, timeout=0.0)
    original_writer = CodexConfigLock._write_owner_diagnostics

    def fail_owner_diagnostics(_self: CodexConfigLock, _fd: int, _pid: int) -> None:
        raise RuntimeError("owner diagnostics failed")

    monkeypatch.setattr(CodexConfigLock, "_write_owner_diagnostics", fail_owner_diagnostics)
    with pytest.raises(RuntimeError, match="owner diagnostics failed"):
        failed_lock.acquire()

    monkeypatch.setattr(CodexConfigLock, "_write_owner_diagnostics", original_writer)
    reacquired = CodexConfigLock(config_path, timeout=0.0)
    try:
        assert reacquired.acquire() is reacquired
    finally:
        reacquired.release()
