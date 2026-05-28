"""Tests for serve_with_signal_guard dispatch-aware deferral."""

from __future__ import annotations

import os
import signal

import anyio
import pytest

from autoskillit.cli._serve_guard import serve_with_signal_guard

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
                dispatch_activity_check=lambda: _active[0],
                deferral_timeout=3.0,
            )

        elapsed = anyio.current_time() - started_at
        assert elapsed >= 1.5
        assert not _active[0], "dispatch_activity_check should report inactive after deferral"

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
                dispatch_activity_check=lambda: False,
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
                dispatch_activity_check=lambda: True,
                deferral_timeout=2.0,
            )

        elapsed = anyio.current_time() - started_at
        assert 2.0 <= elapsed < 3.5
