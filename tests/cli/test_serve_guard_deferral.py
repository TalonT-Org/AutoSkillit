"""Tests for serve_with_signal_guard dispatch-aware deferral."""

from __future__ import annotations

import inspect
import os
import signal
import time
import uuid
from pathlib import Path

import anyio
import pytest

from autoskillit.cli._serve_guard import serve_with_signal_guard
from autoskillit.cli.app import is_server_active

pytestmark = [pytest.mark.layer("cli"), pytest.mark.medium]


class _FakeMCPServer:
    def __init__(self, event: anyio.Event) -> None:
        self._event = event

    async def run_async(self) -> None:
        await self._event.wait()


class TestServeGuardDeferral:
    @pytest.mark.anyio
    async def test_defers_when_dispatch_active(self) -> None:
        _active = [True]
        done = anyio.Event()
        server = _FakeMCPServer(done)
        started_at = anyio.current_time()

        async def _send_signal_then_release() -> None:
            await anyio.sleep(0.2)
            os.kill(os.getpid(), signal.SIGTERM)
            await anyio.sleep(1.5)
            _active[0] = False
            done.set()

        async with anyio.create_task_group() as tg:
            tg.start_soon(_send_signal_then_release)
            await serve_with_signal_guard(
                server,
                activity_check=lambda: _active[0],
                deferral_timeout=3.0,
            )

        elapsed = anyio.current_time() - started_at
        assert elapsed >= 1.5
        assert not _active[0], "activity_check should report inactive after deferral"

    @pytest.mark.anyio
    async def test_cancels_immediately_when_no_dispatch(self) -> None:
        done = anyio.Event()
        server = _FakeMCPServer(done)
        started_at = anyio.current_time()

        async def _send_signal() -> None:
            await anyio.sleep(0.2)
            os.kill(os.getpid(), signal.SIGTERM)

        async with anyio.create_task_group() as tg:
            tg.start_soon(_send_signal)
            await serve_with_signal_guard(
                server,
                activity_check=lambda: False,
                deferral_timeout=3.0,
            )

        elapsed = anyio.current_time() - started_at
        assert elapsed < 2.0

    @pytest.mark.anyio
    async def test_timeout_forces_shutdown(self) -> None:
        done = anyio.Event()
        server = _FakeMCPServer(done)
        started_at = anyio.current_time()

        async def _send_signal() -> None:
            await anyio.sleep(0.2)
            os.kill(os.getpid(), signal.SIGTERM)
            await anyio.sleep(3.0)
            done.set()

        async with anyio.create_task_group() as tg:
            tg.start_soon(_send_signal)
            await serve_with_signal_guard(
                server,
                activity_check=lambda: True,
                deferral_timeout=2.0,
            )

        elapsed = anyio.current_time() - started_at
        assert 2.0 <= elapsed < 3.5


class TestActivityCheckCompleteness:
    def test_activity_check_true_when_execution_marker_exists(self, tmp_path: Path) -> None:
        marker_name = f"run-skill-in-progress-sess-{uuid.uuid4()}.marker"
        (tmp_path / marker_name).touch()
        assert is_server_active(marker_dir=tmp_path, fleet_lock=None) is True

    @pytest.mark.anyio
    async def test_activity_check_true_when_fleet_lock_active(self) -> None:
        from autoskillit.fleet import FleetSemaphore

        sem = FleetSemaphore()
        await sem.acquire()
        try:
            assert is_server_active(marker_dir=None, fleet_lock=sem) is True
        finally:
            sem.release()

    def test_activity_check_false_when_both_idle(self, tmp_path: Path) -> None:
        from autoskillit.fleet import FleetSemaphore

        assert is_server_active(marker_dir=tmp_path, fleet_lock=FleetSemaphore()) is False

    def test_activity_check_true_when_marker_within_age(self, tmp_path: Path) -> None:
        marker = tmp_path / f"run-skill-in-progress-sess-{uuid.uuid4()}.marker"
        marker.touch()
        os.utime(marker, (time.time() - 50, time.time() - 50))
        assert is_server_active(marker_dir=tmp_path, fleet_lock=None) is True

    def test_activity_check_false_when_marker_expired(self, tmp_path: Path) -> None:
        marker = tmp_path / f"run-skill-in-progress-sess-{uuid.uuid4()}.marker"
        marker.touch()
        os.utime(marker, (time.time() - 120, time.time() - 120))
        assert is_server_active(marker_dir=tmp_path, fleet_lock=None) is False


class TestParameterNaming:
    def test_serve_with_signal_guard_accepts_activity_check_param(self) -> None:
        sig = inspect.signature(serve_with_signal_guard)
        assert "activity_check" in sig.parameters
