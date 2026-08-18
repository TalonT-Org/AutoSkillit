"""Tests for server._progress_heartbeat.progress_heartbeat."""

from __future__ import annotations

import math
from unittest.mock import AsyncMock, MagicMock

import anyio
import pytest

from autoskillit.server._progress_heartbeat import progress_heartbeat

pytestmark = [pytest.mark.layer("server"), pytest.mark.small]


def _fake_ctx() -> MagicMock:
    return MagicMock(report_progress=AsyncMock())


@pytest.mark.anyio
async def test_ticks_fire_at_least_four_times_over_a_slow_body():
    ctx = _fake_ctx()

    # 0.005s interval over 0.06s body yields 12 expected ticks; the >= 4 floor
    # is generous enough for `pytest -n 4` scheduling jitter without flaking.
    async with progress_heartbeat(ctx, interval=0.005, message="still running"):
        await anyio.sleep(0.06)

    assert ctx.report_progress.await_count >= 4


@pytest.mark.anyio
async def test_no_further_ticks_after_context_exits():
    ctx = _fake_ctx()

    async with progress_heartbeat(ctx, interval=0.01, message="still running"):
        await anyio.sleep(0.03)

    count_at_exit = ctx.report_progress.await_count
    assert count_at_exit >= 1
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
    assert ctx.report_progress.await_count >= 1


@pytest.mark.anyio
async def test_non_timeout_exception_from_body_propagates_with_original_type():
    ctx = _fake_ctx()

    class _DistinctiveError(RuntimeError):
        pass

    with pytest.raises(_DistinctiveError):
        async with progress_heartbeat(ctx, interval=30.0):
            raise _DistinctiveError("boom")


@pytest.mark.anyio
async def test_base_exception_from_body_propagates_with_original_type():
    """A bare BaseException (not an Exception subclass) is wrapped by anyio in a
    BaseExceptionGroup, not an ExceptionGroup — the narrower except clause would
    miss it entirely (regression: this escaped uncaught before the fix)."""
    ctx = _fake_ctx()

    class _Sentinel(BaseException):
        pass

    with pytest.raises(_Sentinel):
        async with progress_heartbeat(ctx, interval=30.0):
            raise _Sentinel("boom")


@pytest.mark.parametrize(
    "bad_interval",
    [0.0, -1.0, math.inf, -math.inf, math.nan],
)
@pytest.mark.anyio
async def test_interval_must_be_finite_positive(bad_interval):
    ctx = _fake_ctx()
    with pytest.raises(ValueError, match="interval"):
        async with progress_heartbeat(ctx, interval=bad_interval):
            pass
