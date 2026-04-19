"""Tests for franchise package."""

from __future__ import annotations

import inspect

import pytest

pytestmark = [pytest.mark.layer("franchise"), pytest.mark.small]


def test_franchise_package_importable() -> None:
    """franchise package can be imported without error."""
    import autoskillit.franchise  # noqa: F401


def test_asyncio_lock_satisfies_franchise_lock() -> None:
    """asyncio.Lock() is a structural match for FranchiseLock at runtime."""
    import asyncio

    from autoskillit.core import FranchiseLock

    assert isinstance(asyncio.Lock(), FranchiseLock)


def test_franchise_lock_protocol_has_required_methods() -> None:
    """FranchiseLock exposes locked, acquire, and release."""
    from autoskillit.core import FranchiseLock

    members = {name for name, _ in inspect.getmembers(FranchiseLock) if not name.startswith("_")}
    assert {"locked", "acquire", "release"} <= members


def test_franchise_lock_acquire_is_coroutine() -> None:
    """acquire() must be async — asyncio.Lock.acquire is a coroutine."""
    from autoskillit.core import FranchiseLock

    assert inspect.iscoroutinefunction(FranchiseLock.acquire)


def test_tool_context_has_franchise_lock_field() -> None:
    """ToolContext has a franchise_lock field defaulting to None."""
    from autoskillit.pipeline.context import ToolContext

    field_info = ToolContext.__dataclass_fields__["franchise_lock"]
    assert field_info.default is None
