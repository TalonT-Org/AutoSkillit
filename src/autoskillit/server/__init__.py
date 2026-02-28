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

from autoskillit.config import AutomationConfig
from autoskillit.core import get_logger
from autoskillit.pipeline import (  # noqa: F401
    GATED_TOOLS,
    UNGATED_TOOLS,
    DefaultGateState,
    ToolContext,
    gate_error_result,
)

mcp: FastMCP = FastMCP("autoskillit")

_ctx: ToolContext | None = None

logger = get_logger(__name__)


def _initialize(ctx: ToolContext) -> None:
    """Set the server's ToolContext. Called by cli/app.py serve() before mcp.run()."""
    global _ctx
    _ctx = ctx


def _get_ctx() -> ToolContext:
    """Return the active ToolContext. Raises if _initialize() has not been called."""
    if _ctx is None:
        raise RuntimeError(
            "serve() must be called before accessing context. "
            "Call server._initialize(ctx) before mcp.run()."
        )
    return _ctx


def _get_config() -> AutomationConfig:
    """Return the active AutomationConfig from the ToolContext."""
    return _get_ctx().config


def version_info() -> dict:
    """Return version health information for the running server."""
    from autoskillit.version import version_info as _compute_version

    plugin_dir = _ctx.plugin_dir if _ctx is not None else None
    return _compute_version(plugin_dir)


# Import all tool sub-modules to trigger @mcp.tool() registration.
# These imports must come AFTER mcp, _ctx, _get_ctx, _get_config are defined
# because tool modules import `mcp` from this package at import time.
from autoskillit.core import PIPELINE_FORBIDDEN_TOOLS  # noqa: E402, F401
from autoskillit.server import (  # noqa: E402, F401
    helpers,
    prompts,
    tools_clone,
    tools_execution,
    tools_git,
    tools_recipe,
    tools_status,
    tools_workspace,
)
from autoskillit.server._factory import make_context  # noqa: E402, F401

# Re-export all tool functions for backward compatibility with test_server.py
# which does `from autoskillit.server import run_cmd, ...`
from autoskillit.server.helpers import (  # noqa: E402, F401
    _check_dry_walkthrough,
    _require_enabled,
    _run_subprocess,
)
from autoskillit.server.prompts import (  # noqa: E402, F401
    _close_kitchen_handler,
    _open_kitchen_handler,
    close_kitchen,
    open_kitchen,
)
from autoskillit.server.tools_clone import (  # noqa: E402, F401
    clone_repo,
    push_to_remote,
    remove_clone,
)
from autoskillit.server.tools_execution import (  # noqa: E402, F401
    run_cmd,
    run_python,
    run_skill,
    run_skill_retry,
)
from autoskillit.server.tools_git import (  # noqa: E402, F401
    classify_fix,
    merge_worktree,
)
from autoskillit.server.tools_recipe import (  # noqa: E402, F401
    list_recipes,
    load_recipe,
    migrate_recipe,
    validate_recipe,
)
from autoskillit.server.tools_status import (  # noqa: E402, F401
    check_quota,
    get_pipeline_report,
    get_token_summary,
    kitchen_status,
    read_db,
)
from autoskillit.server.tools_workspace import (  # noqa: E402, F401
    reset_test_dir,
    reset_workspace,
    test_check,
)
