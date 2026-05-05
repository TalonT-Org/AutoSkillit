"""T6: Concurrency guard for resume."""

from __future__ import annotations

from pathlib import Path

import pytest

from autoskillit.execution.session._session_state import SessionStateLock

pytestmark = [pytest.mark.layer("execution"), pytest.mark.small]


class TestResumeFileLock:
    def test_concurrent_lock_blocked(self, tmp_path: Path) -> None:
        lock_a = SessionStateLock(tmp_path)
        lock_b = SessionStateLock(tmp_path)
        assert lock_a.acquire() is True
        assert lock_b.acquire() is False
        lock_a.release()

    def test_lock_released_allows_next(self, tmp_path: Path) -> None:
        lock_a = SessionStateLock(tmp_path)
        lock_b = SessionStateLock(tmp_path)
        assert lock_a.acquire() is True
        lock_a.release()
        assert lock_b.acquire() is True
        lock_b.release()

    def test_context_manager_releases(self, tmp_path: Path) -> None:
        with SessionStateLock(tmp_path) as acquired:
            assert acquired is True
        lock_b = SessionStateLock(tmp_path)
        assert lock_b.acquire() is True
        lock_b.release()

    def test_double_release_is_safe(self, tmp_path: Path) -> None:
        lock = SessionStateLock(tmp_path)
        lock.acquire()
        lock.release()
        lock.release()
