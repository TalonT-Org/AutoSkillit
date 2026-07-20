"""MCP notification dispatch and response-size tracking."""

from __future__ import annotations

import functools
import json
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

from anyio import BrokenResourceError as _BrokenResource
from anyio import ClosedResourceError as _ClosedResource

from autoskillit.config import OutputBudgetConfig
from autoskillit.core import RESERVED_LOG_RECORD_KEYS, BackendCapabilities, get_logger
from autoskillit.execution.backends import resolve_effective_delivery_bound
from autoskillit.server._response_budget import (
    bounded_response_budget_failure,
    enforce_response_budget,
)

if TYPE_CHECKING:
    from fastmcp import Context

    from autoskillit.pipeline import ToolContext

logger = get_logger(__name__)


def _get_ctx_or_none() -> ToolContext | None:
    from autoskillit.server._state import _get_ctx_or_none as _ctx_none_fn  # circular-break

    return _ctx_none_fn()


async def _notify(
    ctx: Context,
    level: Literal["info", "error"],
    message: str,
    logger_name: str,
    extra: dict[str, Any] | None = None,
) -> None:
    """Send an MCP progress notification via FastMCP's Context.

    Validates extra dict keys against RESERVED_LOG_RECORD_KEYS before
    dispatching. Raises ValueError if any reserved key is found — this
    surfaces programming errors in tests rather than silently crashing
    at runtime only when DEBUG logging is active.

    Catches (RuntimeError, AttributeError, KeyError) from FastMCP internals:
    - RuntimeError: no active MCP session (Context.session raises)
    - AttributeError: ctx is CurrentContext() sentinel during testing
    - KeyError: makeRecord() collision (defense-in-depth; prevented by validation)
    """
    if extra:
        invalid = RESERVED_LOG_RECORD_KEYS & extra.keys()
        if invalid:
            raise ValueError(
                f"extra dict contains reserved LogRecord keys: {sorted(invalid)!r}. "
                "Rename these keys to avoid stdlib logging collisions."
            )
    try:
        if level == "info":
            await ctx.info(message, logger_name=logger_name, extra=extra)
        elif level == "error":
            await ctx.error(message, logger_name=logger_name, extra=extra)
    except (RuntimeError, AttributeError, KeyError, _ClosedResource, _BrokenResource):
        pass


def track_response_size(
    tool_name: str,
) -> Callable[[Callable[..., Awaitable[Any]]], Callable[..., Awaitable[Any]]]:
    """Decorator: measure the JSON string size of a tool response and record to response_log.

    Last-resort safety net. Tool implementations SHOULD catch exceptions locally
    and emit domain-specific envelopes with more helpful ``user_visible_message``
    values; this decorator only catches what slips through.

    Apply BELOW @mcp.tool() so the wrapped function is what FastMCP registers:

        @mcp.tool(tags={"automation"})
        @track_response_size("get_token_summary")
        async def get_token_summary(...) -> str:
            ...
    """

    def decorator(fn: Callable[..., Awaitable[Any]]) -> Callable[..., Awaitable[Any]]:
        @functools.wraps(fn)
        async def wrapper(*args: Any, **kwargs: Any) -> Any:
            try:
                result = await fn(*args, **kwargs)
            except Exception as exc:
                result = json.dumps(
                    {
                        "success": False,
                        "error": f"{type(exc).__name__}: {exc}",
                        "exit_code": -1,
                        "subtype": "tool_exception",
                        "user_visible_message": (
                            f"An internal error occurred in {tool_name}: "
                            f"{type(exc).__name__}. Run 'autoskillit doctor' or reinstall."
                        ),
                    }
                )
                logger.exception("track_response_size_handler_failed", tool_name=tool_name)
            ctx = _get_ctx_or_none()
            try:
                response_str = result if isinstance(result, str) else json.dumps(result)
            except (TypeError, ValueError):
                logger.warning(
                    "track_response_size_nonserializable_result",
                    tool_name=tool_name,
                )
                response_str = None
            exceeded = False
            try:
                if ctx is not None and response_str is not None:
                    threshold = ctx.config.mcp_response.alert_threshold_tokens
                    exceeded = ctx.response_log.record(
                        tool_name, response_str, alert_threshold_tokens=threshold
                    )
            except Exception:
                logger.warning(
                    "track_response_size_telemetry_failed",
                    tool_name=tool_name,
                )

            configured_budget = (
                getattr(ctx.config, "output_budget", None) if ctx is not None else None
            )
            budget = (
                configured_budget
                if isinstance(configured_budget, OutputBudgetConfig)
                else OutputBudgetConfig()
            )
            temp_dir = getattr(ctx, "temp_dir", None) if ctx is not None else None
            artifact_dir = (
                temp_dir / "responses" / tool_name if isinstance(temp_dir, Path) else None
            )
            effective_delivery_token_limit: int | None = None
            if ctx is not None:
                backend = getattr(ctx, "backend", None)
                caps = getattr(backend, "capabilities", None) if backend is not None else None
                if isinstance(caps, BackendCapabilities):
                    effective_delivery_token_limit = resolve_effective_delivery_bound(caps)
            try:
                result = enforce_response_budget(
                    result,
                    tool_name=tool_name,
                    artifact_dir=artifact_dir,
                    config=budget,
                    effective_delivery_token_limit=effective_delivery_token_limit,
                )
            except Exception:
                logger.error("track_response_size_enforcement_failed", tool_name=tool_name)
                result = bounded_response_budget_failure(
                    result,
                    cause="internal_invariant_failed",
                    tool_name=tool_name,
                    max_bytes=budget.response_max_bytes,
                    original_utf8_bytes=(
                        len(response_str.encode("utf-8")) if response_str is not None else 0
                    ),
                )

            if exceeded:
                try:
                    from fastmcp import Context as FmcpContext

                    mcp_ctx = next(
                        (a for a in args if isinstance(a, FmcpContext)),
                        next(
                            (v for v in kwargs.values() if isinstance(v, FmcpContext)),
                            None,
                        ),
                    )
                    if mcp_ctx is not None:
                        await _notify(
                            mcp_ctx,
                            "info",
                            f"MCP tool '{tool_name}' response exceeded "
                            f"{threshold} estimated token threshold",
                            logger_name="autoskillit.server.response_size",
                        )
                except Exception:
                    logger.warning(
                        "track_response_size_notification_failed",
                        tool_name=tool_name,
                    )
            return result

        return wrapper

    return decorator
