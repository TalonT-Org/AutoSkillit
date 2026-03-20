#!/usr/bin/env python3
"""MCP server for orchestrating automated skill-driven workflows.

Kitchen tools (37 gated + 1 headless-tagged) are hidden at startup via FastMCP v3
mcp.disable(tags={'kitchen'}) applied once after all tool modules are imported.
Each new session sees only the 2 free-range tools (open_kitchen and close_kitchen).
Headless sessions (AUTOSKILLIT_HEADLESS=1) pre-reveal only headless-tagged tools
(test_check) via mcp.enable(tags={'headless'}) — not all kitchen tools.
Calling the open_kitchen tool reveals all 38 kitchen-tagged tools for that session
via ctx.enable_components(tags={'kitchen'}).

Transport: stdio (default for FastMCP).
"""

from __future__ import annotations

from fastmcp import FastMCP

from autoskillit.core import get_logger
from autoskillit.pipeline import (  # noqa: F401
    GATED_TOOLS,
    UNGATED_TOOLS,
    DefaultGateState,
    ToolContext,
    gate_error_result,
)
from autoskillit.server._state import (  # noqa: E402, F401
    _ctx,
    _get_config,
    _get_ctx,
    _get_plugin_dir,
    _initialize,
    version_info,
)

mcp: FastMCP = FastMCP("autoskillit")

logger = get_logger(__name__)

__all__ = [
    # The FastMCP application instance — primary artifact of this package
    "mcp",
    # Public utilities consumed by CLI and tests
    "version_info",
    "make_context",
]

# Import all tool sub-modules to trigger @mcp.tool() registration.
# These imports must come AFTER mcp, _get_ctx, _get_config are defined
# because tool modules import `mcp` from this package at import time.
import os  # noqa: E402

from autoskillit.core import PIPELINE_FORBIDDEN_TOOLS  # noqa: E402, F401
from autoskillit.server import (  # noqa: E402, F401
    helpers,
    tools_ci,
    tools_clone,
    tools_execution,
    tools_git,
    tools_github,
    tools_issue_lifecycle,
    tools_kitchen,
    tools_pr_ops,
    tools_recipe,
    tools_status,
    tools_workspace,
)
from autoskillit.server._factory import make_context  # noqa: E402, F401
from autoskillit.server.tools_kitchen import _build_tool_category_listing  # noqa: E402, F401

# Apply global visibility transform: all sessions start with kitchen tools hidden.
# Must appear after all tool module imports so the registered tools are in place.
mcp.disable(tags={"kitchen"})

# Headless sessions (AUTOSKILLIT_HEADLESS=1) pre-reveal only headless-tagged tools
# (test_check) so the session starts with test_check visible without calling open_kitchen.
if os.environ.get("AUTOSKILLIT_HEADLESS") == "1":
    mcp.enable(tags={"headless"})
