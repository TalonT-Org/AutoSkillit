"""Wire-format compatibility middleware for Claude Code MCP client.

Claude Code bug #25081 silently drops ALL tools from servers whose
tools/list response includes ``outputSchema`` or ``title`` fields.
FastMCP 3.2.3+ auto-generates these for typed return values and annotated
tools. This middleware strips only those fields at the wire level.

``annotations`` is intentionally preserved: it carries ``readOnlyHint``
which Claude Code uses to enable parallel tool execution (~7x pipeline
speedup). Stripping annotations forces all tools to run serially.

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

# Fields stripped to avoid Claude Code #25081 tool-list rejection.
# ONLY output_schema and title trigger the bug. annotations is preserved
# because it carries readOnlyHint for parallel execution.
_STRIPPED_FIELDS: dict[str, None] = {"output_schema": None, "title": None}


class ClaudeCodeCompatMiddleware(Middleware):
    """Strip wire fields that trigger Claude Code #25081 tool-list rejection."""

    async def on_list_tools(
        self,
        context: MiddlewareContext[mt.ListToolsRequest],
        call_next: CallNext[mt.ListToolsRequest, Sequence[Tool]],
    ) -> Sequence[Tool]:
        tools = await call_next(context)
        cleaned: list[Tool] = []
        for tool in tools:
            patched = tool.model_copy(update=_STRIPPED_FIELDS)
            cleaned.append(patched)
        return cleaned
