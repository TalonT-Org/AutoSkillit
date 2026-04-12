"""Headless session kitchen visibility via AUTOSKILLIT_HEADLESS=1."""

from __future__ import annotations

import pytest


@pytest.mark.anyio
async def test_mcp_enable_kitchen_reveals_gated_tools(kitchen_enabled) -> None:
    """mcp.enable(tags={'kitchen'}) reveals all GATED_TOOLS to the client.

    Uses FastMCP Client to assert that every tool in GATED_TOOLS is visible
    after mcp.enable(tags={'kitchen'}), which is the manual reveal step used
    in headless sessions.
    """
    from fastmcp.client import Client

    from autoskillit.pipeline.gate import GATED_TOOLS
    from autoskillit.server import mcp

    async with Client(mcp) as client:
        tool_names = {t.name for t in await client.list_tools()}
    assert GATED_TOOLS.issubset(tool_names), f"Missing gated tools: {GATED_TOOLS - tool_names}"
