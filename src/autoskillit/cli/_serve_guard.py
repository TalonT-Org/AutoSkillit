"""Async signal-guarded MCP server bootstrap for the serve() CLI command.

Extracted from cli/app.py to keep that file under the 750-line budget.
Arms anyio.open_signal_receiver *before* mcp.run_async() so that SIGTERM,
SIGINT, and SIGHUP are delivered as scheduled asyncio callbacks rather than
frame-interrupting KeyboardInterrupt exceptions.

SIGHUP is treated as shutdown rather than reload because the server has no
config to reload and is launched by Claude Code with a controlling terminal;
terminal disconnect (SIGHUP) should trigger the same graceful lifespan
teardown as a normal SIGTERM.
"""

from __future__ import annotations

import signal
from typing import Any

import anyio

from autoskillit.core import get_logger

_log = get_logger(__name__)


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
        with anyio.open_signal_receiver(signal.SIGTERM, signal.SIGINT, signal.SIGHUP) as signals:
            task_status.started()  # signal receiver is now armed
            async for sig in signals:
                _log.info("serve_with_signal_guard: received %s — initiating shutdown", sig.name)
                scope.cancel()
                return

    async with anyio.create_task_group() as tg:
        await tg.start(_watch, tg.cancel_scope)
        await mcp_server.run_async()
