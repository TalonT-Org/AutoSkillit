"""Async signal-guarded MCP server bootstrap for the serve() CLI command.

Extracted from cli/app.py to keep that file under the 750-line budget.
Arms anyio.open_signal_receiver *before* mcp.run_async() so that SIGTERM
and SIGINT are delivered as scheduled asyncio callbacks rather than
frame-interrupting KeyboardInterrupt exceptions.
"""

from __future__ import annotations

import signal
from typing import Any

import anyio


async def serve_with_signal_guard(mcp_server: Any) -> None:
    """Run the MCP server with event-loop-routed SIGTERM/SIGINT handling.

    ``tg.start()`` blocks until ``task_status.started()`` fires inside the
    receiver — guaranteeing the handler is active before any readiness
    sentinel can be observed by a test.
    """

    async def _watch(
        scope: anyio.CancelScope,
        *,
        task_status: anyio.abc.TaskStatus = anyio.TASK_STATUS_IGNORED,
    ) -> None:
        with anyio.open_signal_receiver(signal.SIGTERM, signal.SIGINT) as signals:
            task_status.started()  # signal receiver is now armed
            async for _ in signals:
                scope.cancel()
                return

    async with anyio.create_task_group() as tg:
        await tg.start(_watch, tg.cancel_scope)
        await mcp_server.run_async()
