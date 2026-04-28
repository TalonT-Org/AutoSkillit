"""Unit tests for FleetSemaphore (FleetLock semaphore implementation)."""

from __future__ import annotations

import pytest

from autoskillit.core import FleetLock
from autoskillit.server._factory import FleetSemaphore

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
