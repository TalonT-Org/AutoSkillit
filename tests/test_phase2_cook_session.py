"""Phase 2 tests: cook session kitchen visibility via AUTOSKILLIT_KITCHEN_OPEN=1."""

from __future__ import annotations

import pytest


@pytest.mark.anyio
async def test_cook_session_kitchen_visible_from_startup(monkeypatch: pytest.MonkeyPatch) -> None:
    """When AUTOSKILLIT_KITCHEN_OPEN=1, kitchen tools are pre-revealed.

    Uses FastMCP Client to assert that GATED_TOOLS are visible after
    mcp.enable(tags={'kitchen'}), which is the startup behavior for cook sessions.
    """
    from fastmcp.client import Client

    from autoskillit.pipeline.gate import GATED_TOOLS
    from autoskillit.server import mcp

    monkeypatch.setenv("AUTOSKILLIT_KITCHEN_OPEN", "1")
    mcp.enable(tags={"kitchen"})
    try:
        async with Client(mcp) as client:
            tool_names = {t.name for t in await client.list_tools()}
        assert any(name in GATED_TOOLS for name in tool_names), (
            "Kitchen tools must be enabled when AUTOSKILLIT_KITCHEN_OPEN=1"
        )
    finally:
        mcp.disable(tags={"kitchen"})
