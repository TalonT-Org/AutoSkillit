"""Tests for fleet package."""

from __future__ import annotations

import inspect

import pytest

pytestmark = [pytest.mark.layer("fleet"), pytest.mark.small, pytest.mark.feature("fleet")]


def test_fleet_package_importable() -> None:
    """fleet package can be imported without error."""
    import autoskillit.fleet  # noqa: F401

    # Verify gateway exports are accessible
    from autoskillit.fleet import CampaignState, DispatchRecord, read_state  # noqa: F401


def test_asyncio_lock_satisfies_fleet_lock() -> None:
    """asyncio.Lock() is a structural match for FleetLock at runtime.

    isinstance() with @runtime_checkable only verifies that required attribute names
    are present — not signatures. The additional assertion confirms that acquire is
    a coroutine on the concrete implementation, not just a name match.
    """
    import asyncio

    from autoskillit.core import FleetLock

    lock = asyncio.Lock()
    assert isinstance(lock, FleetLock)
    assert inspect.iscoroutinefunction(lock.acquire)


def test_fleet_lock_protocol_has_required_methods() -> None:
    """FleetLock exposes locked, acquire, and release."""
    from autoskillit.core import FleetLock

    members = {name for name, _ in inspect.getmembers(FleetLock) if not name.startswith("_")}
    assert {"locked", "acquire", "release"} <= members


def test_fleet_lock_acquire_is_coroutine() -> None:
    """acquire() must be async — verifies asyncio.Lock().acquire is a coroutine."""
    import asyncio

    assert inspect.iscoroutinefunction(asyncio.Lock().acquire)


def test_headless_executor_protocol_has_dispatch_food_truck() -> None:
    """HeadlessExecutor protocol exposes dispatch_food_truck."""
    from autoskillit.core import HeadlessExecutor

    assert hasattr(HeadlessExecutor, "dispatch_food_truck")
