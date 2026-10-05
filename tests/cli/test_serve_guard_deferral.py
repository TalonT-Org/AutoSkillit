"""Tests for serve_with_signal_guard dispatch-aware deferral."""

from __future__ import annotations

import inspect
import os
import signal
from pathlib import Path

import anyio
import pytest

from autoskillit.cli._serve_guard import serve_with_signal_guard
from autoskillit.cli.app import is_server_active
from autoskillit.core import InFlightOperations

pytestmark = [pytest.mark.layer("cli"), pytest.mark.medium]


class _FakeMCPServer:
    def __init__(self, event: anyio.Event) -> None:
        self._event = event

    async def run_async(self) -> None:
        await self._event.wait()


class _ReturningMCPServer:
    async def run_async(self) -> None:
        return


class TestServeGuardDeferral:
    @pytest.mark.anyio
    async def test_returns_when_server_primary_returns(self) -> None:
        with anyio.fail_after(1.0):
            await serve_with_signal_guard(_ReturningMCPServer())

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
    def test_activity_check_true_when_operation_is_in_flight(self) -> None:
        operations = InFlightOperations()
        operations._enter()
        try:
            assert is_server_active(worker_capacity=None, in_flight_operations=operations) is True
        finally:
            operations._exit()

    @pytest.mark.anyio
    async def test_activity_check_true_when_worker_capacity_active(self) -> None:
        from autoskillit.core import DefaultManagedWorkerCapacity

        capacity = DefaultManagedWorkerCapacity()
        permit = await capacity.acquire("active-dispatch")
        try:
            assert is_server_active(
                worker_capacity=capacity, in_flight_operations=InFlightOperations()
            )
        finally:
            capacity.release(permit)

    def test_activity_check_false_when_both_idle(self) -> None:
        from autoskillit.core import DefaultManagedWorkerCapacity

        assert (
            is_server_active(
                worker_capacity=DefaultManagedWorkerCapacity(),
                in_flight_operations=InFlightOperations(),
            )
            is False
        )

    def test_stray_backend_marker_does_not_make_server_active(self, tmp_path: Path) -> None:
        (tmp_path / "dispatch-in-progress-other-session-marker.marker").touch()
        assert (
            is_server_active(
                worker_capacity=None,
                in_flight_operations=InFlightOperations(),
            )
            is False
        )


class TestParameterNaming:
    def test_serve_with_signal_guard_accepts_activity_check_param(self) -> None:
        sig = inspect.signature(serve_with_signal_guard)
        assert "activity_check" in sig.parameters
