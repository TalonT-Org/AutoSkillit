"""Wire-format compatibility middleware for Claude Code MCP client.

Claude Code bug #25081 silently drops ALL tools from servers whose
tools/list response includes ``outputSchema``, ``annotations``, or ``title``
fields. FastMCP 3.2.3+ auto-generates these for typed return values and
annotated tools. This middleware strips them at the wire level so tool
registration code can use idiomatic FastMCP patterns without worrying
about client-side parser quirks.

Ref: https://github.com/anthropics/claude-code/issues/25081
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import TYPE_CHECKING

from fastmcp.server.middleware import Middleware

if TYPE_CHECKING:
    import mcp.types as mt
    from fastmcp.server.middleware import CallNext, MiddlewareContext
    from fastmcp.tools.tool import Tool


class ClaudeCodeCompatMiddleware(Middleware):
    """Strip wire fields that trigger Claude Code #25081 tool-list rejection."""

    async def on_list_tools(
        self,
        context: MiddlewareContext[mt.ListToolsRequest],
        call_next: CallNext[mt.ListToolsRequest, Sequence[Tool]],
    ) -> Sequence[Tool]:
        tools = await call_next(context)
        for tool in tools:
            tool.output_schema = None
            tool.annotations = None
        return tools
