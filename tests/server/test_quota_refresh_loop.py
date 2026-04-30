"""Tests for _quota_refresh_loop in server/_misc.py."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import pytest

from autoskillit.config.settings import QuotaGuardConfig

pytestmark = [pytest.mark.layer("server"), pytest.mark.small]


@pytest.mark.anyio
async def test_quota_refresh_loop_calls_refresh_at_each_interval(monkeypatch):
    """Loop calls _refresh_quota_cache once per cache_refresh_interval sleep."""
    from autoskillit.server._misc import _quota_refresh_loop

    call_count = 0
    sleep_count = 0

    async def fake_sleep(n):
        nonlocal sleep_count
        sleep_count += 1
        if sleep_count >= 3:
            raise asyncio.CancelledError

    async def fake_refresh(config):
        nonlocal call_count
        call_count += 1

    monkeypatch.setattr("autoskillit.server._misc.asyncio.sleep", fake_sleep)
    monkeypatch.setattr("autoskillit.server._misc._refresh_quota_cache", fake_refresh)

    config = QuotaGuardConfig(cache_refresh_interval=240)
    with pytest.raises(asyncio.CancelledError):
        await _quota_refresh_loop(config)

    assert call_count == 2  # one refresh per completed sleep


@pytest.mark.anyio
async def test_quota_refresh_loop_exits_cleanly_on_cancel(monkeypatch):
    """CancelledError from asyncio.sleep propagates; loop does not swallow it."""
    from autoskillit.server._misc import _quota_refresh_loop

    async def immediate_cancel(n):
        raise asyncio.CancelledError

    monkeypatch.setattr("autoskillit.server._misc.asyncio.sleep", immediate_cancel)
    monkeypatch.setattr("autoskillit.server._misc._refresh_quota_cache", AsyncMock())
    task = asyncio.create_task(_quota_refresh_loop(QuotaGuardConfig()))
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


@pytest.mark.anyio
async def test_quota_refresh_loop_continues_after_refresh_exception(monkeypatch):
    """A transient error in _refresh_quota_cache does not kill the loop."""
    from autoskillit.server._misc import _quota_refresh_loop

    call_count = 0
    sleep_count = 0

    async def fake_sleep(n):
        nonlocal sleep_count
        sleep_count += 1
        if sleep_count >= 3:
            raise asyncio.CancelledError

    async def flaky_refresh(config):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise OSError("network blip")

    monkeypatch.setattr("autoskillit.server._misc.asyncio.sleep", fake_sleep)
    monkeypatch.setattr("autoskillit.server._misc._refresh_quota_cache", flaky_refresh)

    with pytest.raises(asyncio.CancelledError):
        await _quota_refresh_loop(QuotaGuardConfig())

    assert call_count == 2  # loop continued after the first OSError
