#!/usr/bin/env python3
"""MCP server for orchestrating automated skill-driven workflows.

All tools are gated by default and require the user to type the
open_kitchen prompt to activate. The prompt name depends on how the
server is loaded (plugin vs --plugin-dir). This uses MCP prompts
(user-controlled, model cannot invoke) to set an in-memory flag
that each tool checks before executing. The gate survives
--dangerously-skip-permissions.

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
    _get_config,
    _get_ctx,
    _initialize,
    version_info,
)

mcp: FastMCP = FastMCP("autoskillit")

logger = get_logger(__name__)


# Import all tool sub-modules to trigger @mcp.tool() registration.
# These imports must come AFTER mcp, _get_ctx, _get_config are defined
# because tool modules import `mcp` from this package at import time.
from autoskillit.core import PIPELINE_FORBIDDEN_TOOLS  # noqa: E402, F401
from autoskillit.server import (  # noqa: E402, F401
    helpers,
    prompts,
    tools_clone,
    tools_execution,
    tools_git,
    tools_integrations,
    tools_recipe,
    tools_status,
    tools_workspace,
)
from autoskillit.server._factory import make_context  # noqa: E402, F401
