"""Headless session kitchen visibility via AUTOSKILLIT_HEADLESS=1."""

from __future__ import annotations

import pytest

pytestmark = [pytest.mark.layer("server"), pytest.mark.medium]


@pytest.mark.anyio
async def test_mcp_enable_kitchen_reveals_gated_tools(kitchen_enabled) -> None:
    """mcp.enable(tags={'kitchen'}) reveals all GATED_TOOLS to the client.

    Uses FastMCP Client to assert that every tool in KITCHEN_GATED_TOOLS is visible
    after mcp.enable(tags={'kitchen'}), which is the manual reveal step used
    in headless sessions.
    """
    from fastmcp.client import Client

    from autoskillit.core import EVIDENCE_READER_TOOLS, KITCHEN_GATED_TOOLS
    from autoskillit.server import mcp

    async with Client(mcp) as client:
        tool_names = {t.name for t in await client.list_tools()}

    assert KITCHEN_GATED_TOOLS.issubset(tool_names), (
        f"Missing gated tools: {KITCHEN_GATED_TOOLS - tool_names}"
    )
    assert tool_names.isdisjoint(EVIDENCE_READER_TOOLS)


@pytest.mark.anyio
async def test_mcp_enable_kitchen_reveals_no_fleet_mutation_tools(kitchen_enabled) -> None:
    """Kitchen visibility includes no fleet mutation tools."""
    from fastmcp.client import Client

    from autoskillit.server import mcp
    from tests.server._session_catalogs import assert_no_fleet_mutation_leak

    async with Client(mcp) as client:
        tool_names = {t.name for t in await client.list_tools()}
    assert_no_fleet_mutation_leak(tool_names)
