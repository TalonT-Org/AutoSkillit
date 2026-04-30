"""Unit tests for FleetSemaphore (FleetLock semaphore implementation)."""

from __future__ import annotations

import asyncio

import pytest

from autoskillit.core import FleetLock
from autoskillit.fleet import FleetSemaphore

pytestmark = [pytest.mark.layer("fleet"), pytest.mark.small, pytest.mark.feature("fleet")]


@pytest.mark.anyio
async def test_fleet_semaphore_max1_at_capacity_after_acquire():
    """max=1 semaphore reports at_capacity True after single acquire."""
    s = FleetSemaphore(max_concurrent=1)
    assert not s.at_capacity()
    await s.acquire()
    assert s.at_capacity()
    s.release()
    assert not s.at_capacity()


@pytest.mark.anyio
async def test_fleet_semaphore_max3_allows_three_concurrent():
    """max=3 semaphore allows 3 acquires without blocking."""
    s = FleetSemaphore(max_concurrent=3)
    await s.acquire()
    await s.acquire()
    await s.acquire()
    assert s.at_capacity()
    assert s.active_count == 3
    s.release()
    assert not s.at_capacity()
    assert s.active_count == 2


@pytest.mark.anyio
async def test_fleet_semaphore_active_count_tracks_correctly():
    """active_count increments on acquire and decrements on release."""
    s = FleetSemaphore(max_concurrent=5)
    assert s.active_count == 0
    await s.acquire()
    await s.acquire()
    assert s.active_count == 2
    s.release()
    assert s.active_count == 1


def test_fleet_semaphore_max_concurrent_property():
    """max_concurrent returns the constructor argument."""
    s = FleetSemaphore(max_concurrent=7)
    assert s.max_concurrent == 7


def test_fleet_semaphore_satisfies_fleet_lock_protocol():
    """FleetSemaphore is a structural FleetLock at runtime."""
    s = FleetSemaphore(max_concurrent=1)
    assert isinstance(s, FleetLock)


@pytest.mark.anyio
async def test_fleet_semaphore_max1_equivalent_to_serial():
    """FleetSemaphore(1) rejects a second concurrent caller (same as asyncio.Lock)."""
    s = FleetSemaphore(max_concurrent=1)
    await s.acquire()
    assert s.at_capacity()  # second would be refused at call site
    s.release()
    assert not s.at_capacity()


class TestFleetSemaphoreConstructorGuard:
    def test_max_concurrent_zero_raises(self) -> None:
        with pytest.raises(ValueError):
            FleetSemaphore(max_concurrent=0)

    def test_max_concurrent_negative_raises(self) -> None:
        with pytest.raises(ValueError):
            FleetSemaphore(max_concurrent=-1)


class TestExecuteDispatchCancelledErrorLockRelease:
    @pytest.mark.anyio
    async def test_cancelled_error_propagates_and_releases_lock(
        self, tool_ctx, monkeypatch
    ) -> None:
        from tests.fleet._helpers import _setup_dispatch

        _setup_dispatch(tool_ctx, monkeypatch)
        fleet_lock = tool_ctx.fleet_lock

        async def _raise_cancelled(**_kwargs):
            raise asyncio.CancelledError

        monkeypatch.setattr("autoskillit.fleet._api._run_dispatch", _raise_cancelled)

        with pytest.raises(asyncio.CancelledError):
            from autoskillit.fleet import execute_dispatch

            await execute_dispatch(
                tool_ctx=tool_ctx,
                recipe="test-recipe",
                task="do something",
                ingredients=None,
                dispatch_name="test-dispatch",
                timeout_sec=None,
                prompt_builder=lambda *a, **kw: "prompt",
                quota_checker=lambda *a, **kw: None,
                quota_refresher=lambda *a, **kw: None,
            )

        assert fleet_lock.active_count == 0
