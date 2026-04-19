"""Tests for franchise package."""

from __future__ import annotations

import inspect

import pytest

pytestmark = [pytest.mark.layer("franchise"), pytest.mark.small]


def test_franchise_package_importable() -> None:
    """franchise package can be imported without error."""
    import autoskillit.franchise  # noqa: F401

    # Verify gateway exports are accessible
    from autoskillit.franchise import CampaignState, DispatchRecord, read_state  # noqa: F401


def test_asyncio_lock_satisfies_franchise_lock() -> None:
    """asyncio.Lock() is a structural match for FranchiseLock at runtime.

    isinstance() with @runtime_checkable only verifies that required attribute names
    are present — not signatures. The additional assertion confirms that acquire is
    a coroutine on the concrete implementation, not just a name match.
    """
    import asyncio

    from autoskillit.core import FranchiseLock

    lock = asyncio.Lock()
    assert isinstance(lock, FranchiseLock)
    assert inspect.iscoroutinefunction(lock.acquire)


def test_franchise_lock_protocol_has_required_methods() -> None:
    """FranchiseLock exposes locked, acquire, and release."""
    from autoskillit.core import FranchiseLock

    members = {name for name, _ in inspect.getmembers(FranchiseLock) if not name.startswith("_")}
    assert {"locked", "acquire", "release"} <= members


def test_franchise_lock_acquire_is_coroutine() -> None:
    """acquire() must be async — verifies asyncio.Lock().acquire is a coroutine."""
    import asyncio

    assert inspect.iscoroutinefunction(asyncio.Lock().acquire)


def test_headless_executor_protocol_has_dispatch_food_truck() -> None:
    """HeadlessExecutor protocol exposes dispatch_food_truck."""
    from autoskillit.core import HeadlessExecutor

    assert hasattr(HeadlessExecutor, "dispatch_food_truck")


def test_default_executor_satisfies_protocol_with_dispatch(minimal_ctx) -> None:
    """DefaultHeadlessExecutor satisfies HeadlessExecutor protocol."""
    from autoskillit.core import HeadlessExecutor
    from autoskillit.execution.headless import DefaultHeadlessExecutor

    executor = DefaultHeadlessExecutor(minimal_ctx)
    assert isinstance(executor, HeadlessExecutor)
    assert hasattr(executor, "dispatch_food_truck")
