"""Tests for server._progress_heartbeat.progress_heartbeat."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import anyio
import pytest

from autoskillit.server._progress_heartbeat import progress_heartbeat

pytestmark = [pytest.mark.layer("server"), pytest.mark.small]


def _fake_ctx() -> MagicMock:
    return MagicMock(report_progress=AsyncMock())


@pytest.mark.anyio
async def test_ticks_fire_at_least_twice_over_a_slow_body():
    ctx = _fake_ctx()

    async with progress_heartbeat(ctx, interval=0.01, message="still running"):
        await anyio.sleep(0.05)

    assert ctx.report_progress.await_count >= 2


@pytest.mark.anyio
async def test_no_further_ticks_after_context_exits():
    ctx = _fake_ctx()

    async with progress_heartbeat(ctx, interval=0.01, message="still running"):
        await anyio.sleep(0.03)

    count_at_exit = ctx.report_progress.await_count
    await anyio.sleep(0.05)

    assert ctx.report_progress.await_count == count_at_exit


@pytest.mark.anyio
async def test_ticker_exception_is_swallowed_without_affecting_body():
    ctx = _fake_ctx()
    ctx.report_progress.side_effect = RuntimeError("no active MCP session")

    async with progress_heartbeat(ctx, interval=0.01, message="still running"):
        await anyio.sleep(0.03)
        body_ran = True

    assert body_ran is True


@pytest.mark.anyio
async def test_non_timeout_exception_from_body_propagates_with_original_type():
    ctx = _fake_ctx()

    class _DistinctiveError(RuntimeError):
        pass

    with pytest.raises(_DistinctiveError):
        async with progress_heartbeat(ctx, interval=30.0):
            raise _DistinctiveError("boom")


@pytest.mark.anyio
async def test_body_exception_is_not_wrapped_in_exception_group():
    ctx = _fake_ctx()

    class _DistinctiveError(RuntimeError):
        pass

    caught: Exception | None = None
    try:
        async with progress_heartbeat(ctx, interval=30.0):
            raise _DistinctiveError("boom")
    except Exception as exc:  # noqa: BLE001 - intentionally broad to inspect the type
        caught = exc

    assert type(caught) is _DistinctiveError
    assert not isinstance(caught, ExceptionGroup)
