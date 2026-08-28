"""Unit tests for the shared owner-bound managed worker capacity."""

from __future__ import annotations

import pytest

from autoskillit.core import (
    DefaultManagedWorkerCapacity,
    ManagedWorkerCapacity,
    ManagedWorkerCapacityError,
)

pytestmark = [pytest.mark.layer("fleet"), pytest.mark.small, pytest.mark.feature("fleet")]


@pytest.mark.anyio
async def test_capacity_max1_at_capacity_after_acquire():
    """max=1 capacity reports at_capacity True after one owned acquisition."""
    s = DefaultManagedWorkerCapacity(max_concurrent=1)
    assert not s.at_capacity()
    permit = await s.acquire("fleet-dispatch")
    assert s.at_capacity()
    s.release(permit)
    assert not s.at_capacity()


@pytest.mark.anyio
async def test_capacity_max3_allows_three_concurrent():
    """max=3 semaphore allows 3 acquires without blocking."""
    s = DefaultManagedWorkerCapacity(max_concurrent=3)
    permits = [await s.acquire(f"dispatch-{index}") for index in range(3)]
    assert s.at_capacity()
    assert s.active_count == 3
    s.release(permits[0])
    assert not s.at_capacity()
    assert s.active_count == 2


@pytest.mark.anyio
async def test_capacity_active_count_tracks_correctly():
    """active_count increments on acquire and decrements on release."""
    s = DefaultManagedWorkerCapacity(max_concurrent=5)
    assert s.active_count == 0
    first = await s.acquire("one")
    await s.acquire("two")
    assert s.active_count == 2
    s.release(first)
    assert s.active_count == 1


def test_capacity_max_concurrent_property():
    """max_concurrent returns the constructor argument."""
    s = DefaultManagedWorkerCapacity(max_concurrent=7)
    assert s.max_concurrent == 7


def test_capacity_satisfies_protocol():
    """The concrete authority is a structural ManagedWorkerCapacity."""
    s = DefaultManagedWorkerCapacity(max_concurrent=1)
    assert isinstance(s, ManagedWorkerCapacity)


@pytest.mark.anyio
async def test_capacity_max1_equivalent_to_serial():
    """A held permit makes the single-capacity authority unavailable."""
    s = DefaultManagedWorkerCapacity(max_concurrent=1)
    permit = await s.acquire("first")
    assert s.at_capacity()  # second would be refused at call site
    s.release(permit)
    assert not s.at_capacity()


class TestManagedWorkerCapacityConstructorGuard:
    def test_max_concurrent_zero_raises(self) -> None:
        with pytest.raises(ValueError):
            DefaultManagedWorkerCapacity(max_concurrent=0)

    def test_max_concurrent_negative_raises(self) -> None:
        with pytest.raises(ValueError):
            DefaultManagedWorkerCapacity(max_concurrent=-1)


@pytest.mark.anyio
async def test_capacity_acquire_raises_timeout():
    """acquire() raises TimeoutError when timeout expires."""
    s = DefaultManagedWorkerCapacity(max_concurrent=1, timeout=0.05)
    permit = await s.acquire("first")
    with pytest.raises(TimeoutError):
        await s.acquire("second")
    s.release(permit)


@pytest.mark.anyio
async def test_capacity_acquire_succeeds_within_timeout():
    """acquire() succeeds when slot becomes available before timeout."""
    import asyncio

    s = DefaultManagedWorkerCapacity(max_concurrent=1, timeout=5.0)
    first = await s.acquire("first")

    async def release_soon():
        await asyncio.sleep(0.05)
        s.release(first)

    task = asyncio.create_task(release_soon())
    try:
        second = await s.acquire("second")
        assert s.active_count == 1
        s.release(second)
    finally:
        if not task.done():
            task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass


@pytest.mark.anyio
async def test_capacity_no_timeout_is_default():
    """timeout=None means no timeout (backward compat)."""
    s = DefaultManagedWorkerCapacity(max_concurrent=1)
    assert s.timeout is None


@pytest.mark.anyio
async def test_capacity_rejects_duplicate_or_foreign_release():
    capacity = DefaultManagedWorkerCapacity(max_concurrent=1)
    permit = await capacity.acquire(("batch", "assignment", "run"))
    with pytest.raises(ManagedWorkerCapacityError):
        await capacity.acquire(("batch", "assignment", "run"))
    capacity.release(permit)
    with pytest.raises(ManagedWorkerCapacityError):
        capacity.release(permit)


@pytest.mark.anyio
async def test_capacity_reconfigures_without_orphaning_held_permit():
    capacity = DefaultManagedWorkerCapacity(max_concurrent=2)
    permit = await capacity.acquire("durable-dispatch")
    capacity.reconfigure(max_concurrent=1, timeout=0.1)
    assert capacity.active_count == 1
    assert capacity.at_capacity()
    capacity.release(permit)
    assert not capacity.at_capacity()
