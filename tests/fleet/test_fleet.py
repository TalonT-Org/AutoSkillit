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


def test_managed_worker_capacity_satisfies_protocol() -> None:
    """The process-wide capacity implementation is a structural protocol match."""
    from autoskillit.core import DefaultManagedWorkerCapacity, ManagedWorkerCapacity

    s = DefaultManagedWorkerCapacity(max_concurrent=1)
    assert isinstance(s, ManagedWorkerCapacity)
    assert inspect.iscoroutinefunction(s.acquire)


def test_managed_worker_capacity_protocol_has_required_methods() -> None:
    """ManagedWorkerCapacity exposes owner-bound acquisition and release."""
    from autoskillit.core import ManagedWorkerCapacity

    members = {
        name for name, _ in inspect.getmembers(ManagedWorkerCapacity) if not name.startswith("_")
    }
    assert "at_capacity" in members
    assert "acquire" in members
    assert "release" in members
    assert "active_count" in members
    assert "max_concurrent" in members
    assert "locked" not in members


def test_managed_worker_capacity_acquire_is_coroutine() -> None:
    """acquire() is async on the concrete managed capacity implementation."""
    from autoskillit.core import DefaultManagedWorkerCapacity

    assert inspect.iscoroutinefunction(DefaultManagedWorkerCapacity(max_concurrent=1).acquire)


def test_headless_executor_protocol_has_dispatch_food_truck() -> None:
    """HeadlessExecutor protocol exposes dispatch_food_truck."""
    from autoskillit.core import HeadlessExecutor

    assert hasattr(HeadlessExecutor, "dispatch_food_truck")
