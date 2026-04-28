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


def test_fleet_semaphore_satisfies_fleet_lock() -> None:
    """FleetSemaphore is a structural match for FleetLock at runtime."""
    from autoskillit.core import FleetLock
    from autoskillit.fleet import FleetSemaphore

    s = FleetSemaphore(max_concurrent=1)
    assert isinstance(s, FleetLock)
    assert inspect.iscoroutinefunction(s.acquire)


def test_fleet_lock_protocol_has_required_methods() -> None:
    """FleetLock exposes at_capacity, acquire, release, active_count, max_concurrent."""
    from autoskillit.core import FleetLock

    members = {name for name, _ in inspect.getmembers(FleetLock) if not name.startswith("_")}
    assert "at_capacity" in members
    assert "acquire" in members
    assert "release" in members
    assert "active_count" in members
    assert "max_concurrent" in members
    assert "locked" not in members


def test_fleet_lock_acquire_is_coroutine() -> None:
    """acquire() must be async — verifies FleetSemaphore.acquire is a coroutine."""
    from autoskillit.fleet import FleetSemaphore

    assert inspect.iscoroutinefunction(FleetSemaphore(max_concurrent=1).acquire)


def test_headless_executor_protocol_has_dispatch_food_truck() -> None:
    """HeadlessExecutor protocol exposes dispatch_food_truck."""
    from autoskillit.core import HeadlessExecutor

    assert hasattr(HeadlessExecutor, "dispatch_food_truck")
