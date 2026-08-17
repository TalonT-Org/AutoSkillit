"""Periodic MCP progress notifications for long-blocking tool call spans."""

from __future__ import annotations

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
    ``dispatch_food_truck``) against client-side idle-abort: an MCP client with
    no liveness signal on a blocking call may hard-abort the connection well
    before the server-side ``anyio.fail_after`` deadline fires.
    ``Context.report_progress`` is a safe no-op when the client sent no
    ``progressToken`` (no capability branching required).

    ``anyio.create_task_group()`` wraps any exception escaping the ``async with``
    body in an ``ExceptionGroup`` — even for a single exception — so this unwraps
    a single-item group before re-raising, mirroring the existing pattern at
    ``fleet/_api.py``'s ``execute_dispatch()``. Without this, callers that inspect
    ``type(exception).__name__`` directly (``SkillResult.crashed()``,
    ``dispatch_food_truck``'s ``fleet_error()``) would see ``ExceptionGroup``
    instead of the wrapped body's real exception type.
    """
    try:
        async with anyio.create_task_group() as tg:
            tg.start_soon(_tick, ctx, interval, message)
            yield
            tg.cancel_scope.cancel()
    except ExceptionGroup as exc:
        if len(exc.exceptions) == 1:
            raise exc.exceptions[0] from None
        raise
