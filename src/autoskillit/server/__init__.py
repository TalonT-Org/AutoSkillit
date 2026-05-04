"""MCP server for orchestrating automated skill-driven workflows.

Kitchen tools (48 kitchen-tagged: 47 gated + 1 headless-tagged) are hidden at startup
via FastMCP v3 mcp.disable(tags={'kitchen'}) applied once after all tool modules are
imported. Each new session sees only the 4 free-range tools (open_kitchen, close_kitchen,
disable_quota_guard, and reload_session).

Startup tag visibility is determined by AUTOSKILLIT_SESSION_TYPE (3-branch dispatch):
  FLEET — fleet-tagged tools pre-revealed
  ORCHESTRATOR + HEADLESS=1 — all kitchen-tagged tools pre-revealed
  SKILL + HEADLESS=1 — headless-tagged tools (test_check) pre-revealed
  ORCHESTRATOR/SKILL (interactive) — no pre-reveal; open_kitchen unlocks

Calling the open_kitchen tool reveals all kitchen-tagged tools for that session
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
from autoskillit.server._lifespan import (  # noqa: F401
    _autoskillit_lifespan,
)
from autoskillit.server._state import (  # noqa: E402, F401
    _ctx,
    _get_config,
    _get_ctx,
    _get_plugin_dir,
    _initialize,
    version_info,
)

mcp: FastMCP = FastMCP("autoskillit", lifespan=_autoskillit_lifespan)

logger = get_logger(__name__)

__all__ = [
    # The FastMCP application instance — primary artifact of this package
    "mcp",
    # Public utilities consumed by CLI and tests
    "version_info",
    "make_context",
    # Wire-format compatibility middleware
    "ClaudeCodeCompatMiddleware",
    # Session-type visibility dispatcher (callable by tests)
    "_apply_session_type_visibility",
]

# Import all tool sub-modules to trigger @mcp.tool() registration.
# These imports must come AFTER mcp, _get_ctx, _get_config are defined
# because tool modules import `mcp` from this package at import time.
from autoskillit.core import PIPELINE_FORBIDDEN_TOOLS  # noqa: E402, F401
from autoskillit.server import (  # noqa: E402, F401
    _misc,
    _notify,
)
from autoskillit.server._factory import make_context  # noqa: E402, F401
from autoskillit.server._session_type import _apply_session_type_visibility  # noqa: E402, F401
from autoskillit.server.tools import (  # noqa: E402, F401
    tools_ci as _tools_ci,
)
from autoskillit.server.tools import (  # noqa: E402, F401
    tools_ci_merge_queue as _tools_ci_merge_queue,
)
from autoskillit.server.tools import (  # noqa: E402, F401
    tools_ci_watch as _tools_ci_watch,
)
from autoskillit.server.tools import (  # noqa: E402, F401
    tools_clone as _tools_clone,
)
from autoskillit.server.tools import (  # noqa: E402, F401
    tools_execution as _tools_execution,
)
from autoskillit.server.tools import (  # noqa: E402, F401
    tools_git as _tools_git,
)
from autoskillit.server.tools import (  # noqa: E402, F401
    tools_github as _tools_github,
)
from autoskillit.server.tools import (  # noqa: E402, F401
    tools_issue_composite as _tools_issue_composite,
)
from autoskillit.server.tools import (  # noqa: E402, F401
    tools_issue_lifecycle as _tools_issue_lifecycle,
)
from autoskillit.server.tools import (  # noqa: E402, F401
    tools_kitchen as _tools_kitchen,
)
from autoskillit.server.tools import (  # noqa: E402, F401
    tools_pr_ops as _tools_pr_ops,
)
from autoskillit.server.tools import (  # noqa: E402, F401
    tools_recipe as _tools_recipe,
)
from autoskillit.server.tools import (  # noqa: E402, F401
    tools_status as _tools_status,
)
from autoskillit.server.tools import (  # noqa: E402, F401
    tools_workspace as _tools_workspace,
)
from autoskillit.server.tools.tools_kitchen import _build_tool_category_listing  # noqa: E402, F401

# Apply global visibility transform: all sessions start with kitchen tools hidden.
# Must appear after all tool module imports so the registered tools are in place.
mcp.disable(tags={"kitchen"})

# Wire-format sanitization: strip fields that trigger Claude Code #25081
# (silent full-tool-list rejection when outputSchema/annotations are present).
from autoskillit.server._wire_compat import ClaudeCodeCompatMiddleware  # noqa: E402

mcp.add_middleware(ClaudeCodeCompatMiddleware())

_apply_session_type_visibility()
