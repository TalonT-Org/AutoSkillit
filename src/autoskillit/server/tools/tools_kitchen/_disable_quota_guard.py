"""disable_quota_guard tool — session-scoped quota-guard override."""

from __future__ import annotations

import json

from autoskillit.core import get_logger
from autoskillit.server import mcp
from autoskillit.server._guards import _require_orchestrator_exact
from autoskillit.server._notify import track_response_size
from autoskillit.server.tools._cancellation_shield import _cancellation_shield

logger = get_logger(__name__)


@mcp.tool(
    tags={"autoskillit"}, annotations={"readOnlyHint": True}, meta={"anthropic/alwaysLoad": True}
)
@_cancellation_shield()
@track_response_size("disable_quota_guard")
async def disable_quota_guard() -> str:
    """Disable the quota guard for the remainder of this kitchen session.

    The quota guard blocks run_skill calls when API utilization exceeds a
    threshold. Invoke this tool when you decide the work is worth the quota
    spend and want to override the guard for the current session.

    The caller-session disable is recorded by a PostToolUse hook that reads
    the caller's ``session_id`` from the hook event. The MCP tool itself
    only enforces the local-server lifecycle (orchestrator-exact guard,
    open-kitchen gate) and returns success. The hook writes the marker
    immediately after this response is rendered.

    Session-scoped only: the guard re-activates when the kitchen is closed
    and reopened. Does not modify persistent configuration.

    Never raises.
    """
    try:
        if (h := _require_orchestrator_exact("disable_quota_guard")) is not None:
            return h
        from autoskillit.server import _get_ctx  # circular-break

        ctx = _get_ctx()
        if not ctx.gate.enabled:
            return json.dumps(
                {
                    "success": False,
                    "error": "Kitchen is not open — gate is closed.",
                }
            )
        return json.dumps(
            {
                "success": True,
                "content": (
                    "Quota guard disabled for this session. "
                    "run_skill calls will no longer be blocked by quota checks."
                ),
            }
        )
    except Exception as exc:
        logger.error("disable_quota_guard unhandled exception", exc_info=True)
        return json.dumps({"success": False, "error": f"{type(exc).__name__}: {exc}"})
