"""Cancellation shield decorator for MCP tool handlers.

Catches asyncio.CancelledError at the MCP tool boundary, converting
transport teardown into a structured JSON response. Without this guard
every tool handler that lacks an explicit except-BaseException clause
silently drops the MCP session instead of returning a routable result.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from functools import wraps
from typing import Any, Literal, TypeVar

import anyio

from autoskillit.core import get_logger

logger = get_logger(__name__)

F = TypeVar("F", bound=Callable[..., Any])


def _cancellation_shield(
    result_type: Literal["fleet_error", "run_cmd", "run_python", "generic"] = "generic",
) -> Callable[[F], F]:
    """Decorator that catches asyncio.CancelledError at the MCP tool boundary.

    Apply BELOW @mcp.tool() and ABOVE @track_response_size() so FastMCP
    invokes this wrapper as the outermost layer after @mcp.tool() registration.

    Uses anyio.CancelScope(shield=True) for response construction to prevent
    re-cancellation while building the structured return value.

    result_type controls the response schema:
    - "fleet_error": fleet_error() envelope (dispatch_food_truck, record_gate_dispatch)
    - "run_cmd": {"success": False, "exit_code": -1, "stdout": "", "stderr": ...}
    - "run_python": same as run_cmd
    - "generic" (default): {"success": False, "error": "cancelled", "subtype": "cancelled"}
    """

    def decorator(fn: F) -> F:
        @wraps(fn)
        async def wrapper(*args: Any, **kwargs: Any) -> Any:
            try:
                return await fn(*args, **kwargs)
            except asyncio.CancelledError:
                with anyio.CancelScope(shield=True):
                    logger.warning("mcp_tool_cancelled", tool=fn.__name__)
                    return _build_cancellation_response(result_type)

        return wrapper  # type: ignore[return-value]

    return decorator


def _build_cancellation_response(result_type: str) -> str:
    """Build a structured JSON error response for transport-level CancelledError."""
    msg = "CancelledError: transport teardown"
    if result_type == "fleet_error":
        from autoskillit.core import FleetErrorCode, fleet_error  # noqa: PLC0415

        return fleet_error(FleetErrorCode.FLEET_L3_STARTUP_OR_CRASH, msg)
    if result_type in ("run_cmd", "run_python"):
        return json.dumps({"success": False, "exit_code": -1, "stdout": "", "stderr": msg})
    return json.dumps({"success": False, "error": "cancelled", "subtype": "cancelled"})
