"""Periodic MCP progress notifications for long-blocking tool call spans."""

from __future__ import annotations

import math
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING

import anyio
from anyio import BrokenResourceError as _BrokenResource
from anyio import ClosedResourceError as _ClosedResource

if TYPE_CHECKING:
    from fastmcp import Context

# Matches the exception set `_notify()` swallows (server/_notify.py) — these all
# indicate the client connection is gone or the ctx is a testing sentinel, not a
# programming error worth surfacing.
_SWALLOWED_EXCEPTIONS = (RuntimeError, AttributeError, KeyError, _ClosedResource, _BrokenResource)


async def _tick(ctx: Context, interval: float, message: str) -> None:
    if not math.isfinite(interval) or interval <= 0:
        raise ValueError(
            f"progress_heartbeat interval must be a finite positive number of "
            f"seconds, got {interval!r}"
        )
    progress = 0.0
    while True:
        await anyio.sleep(interval)
        progress += 1
        try:
            await ctx.report_progress(progress, message=message)
        except _SWALLOWED_EXCEPTIONS:
            pass


@asynccontextmanager
async def progress_heartbeat(
    ctx: Context, *, interval: float = 30.0, message: str = "still running"
) -> AsyncGenerator[None]:
    """Emit periodic MCP progress notifications while the wrapped body runs.

    Guards long ``await``s inside MCP tool handlers (``run_skill``,
    ``dispatch_food_truck``) against client-side idle-abort. ``report_progress``
    is a no-op when no ``progressToken`` was supplied, so no capability branching
    is needed.
    """
    try:
        async with anyio.create_task_group() as tg:
            tg.start_soon(_tick, ctx, interval, message)
            yield
            tg.cancel_scope.cancel()
    except BaseExceptionGroup as exc:
        # Unwrap single-item groups so callers inspecting the exception type see
        # the wrapped body's exception, not BaseExceptionGroup. Catches the
        # wider BaseExceptionGroup to cover bodies raising plain BaseException.
        if len(exc.exceptions) == 1:
            raise exc.exceptions[0] from None
        raise
