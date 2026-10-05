"""Publish a deadline-bounded lease around each kitchen tool call."""

from __future__ import annotations

import time
from typing import TYPE_CHECKING

from fastmcp.server.middleware import Middleware
from fastmcp.tools.base import ToolResult

from autoskillit.core import operation_lease
from autoskillit.server.tools._execution_helpers._session_deadline import (
    inherited_session_deadline_epoch,
)

if TYPE_CHECKING:
    import mcp.types as mt
    from fastmcp.server.middleware import CallNext, MiddlewareContext


class OperationLeaseMiddleware(Middleware):
    async def on_call_tool(
        self,
        context: MiddlewareContext[mt.CallToolRequestParams],
        call_next: CallNext[mt.CallToolRequestParams, ToolResult],
    ) -> ToolResult:
        from autoskillit.server.lifecycle._state import (  # circular-break
            _get_ctx_or_none,
        )

        tool_ctx = _get_ctx_or_none()
        if tool_ctx is None:
            return await call_next(context)

        not_after = time.time() + tool_ctx.config.run_skill.mcp_tool_timeout_sec
        inherited_deadline = inherited_session_deadline_epoch()
        if inherited_deadline > 0:
            not_after = min(not_after, inherited_deadline)

        async with operation_lease(
            tool_ctx.operation_lease_channel,
            operation=context.message.name,
            not_after_epoch=not_after,
            registry=tool_ctx.in_flight_operations,
        ):
            return await call_next(context)
