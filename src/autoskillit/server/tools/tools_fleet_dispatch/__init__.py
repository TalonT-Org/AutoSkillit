"""MCP tool handlers: dispatch_food_truck, record_gate_dispatch."""

from __future__ import annotations

# Import sibling submodules for side-effect registration so the @mcp.tool()
# decorators fire and the tools become visible to FastMCP.
from autoskillit.server.tools.tools_fleet_dispatch import (
    _campaign_state,  # noqa: F401
    _provenance,  # noqa: F401
)
from autoskillit.server.tools.tools_fleet_dispatch._handlers import (
    dispatch_food_truck,
    record_gate_dispatch,
)

__all__ = ["dispatch_food_truck", "record_gate_dispatch"]
