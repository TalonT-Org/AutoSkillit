"""Mutable singleton state and context accessor functions for the MCP server.

Extracted from server/__init__.py to keep __init__.py a pure re-export facade
and to give tool modules a stable, non-circular import target for the raw _ctx
sentinel.

This module is the authoritative location for:
  - _ctx: the module-level ToolContext singleton
  - _initialize(ctx): called by cli/app.py serve() before mcp.run()
  - deferred_initialize(ctx, ready_event): background I/O after transport opens
  - _startup_ready: asyncio.Event signalling deferred init completion
  - _get_ctx(): raises RuntimeError if uninitialized (used by gated tools)
  - _get_ctx_or_none(): returns None if uninitialized (used by ungated tools)
  - _get_config(): convenience shortcut to _get_ctx().config
  - version_info(): public server version health query
"""

from __future__ import annotations

import asyncio
from datetime import UTC

from autoskillit.config import AutomationConfig
from autoskillit.core import get_logger
from autoskillit.pipeline import ToolContext

logger = get_logger(__name__)

_ctx: ToolContext | None = None
_startup_ready: asyncio.Event | None = None


def _initialize(ctx: ToolContext) -> None:
    """Set the server's ToolContext. Called by cli/app.py serve() before mcp.run()."""
    global _ctx
    _ctx = ctx

    # Apply server-level subset visibility from config.
    # Uses a deferred local import to avoid module-level circular import
    # (server/__init__.py imports from _state.py at module level).
    if ctx.config.subsets.disabled:
        try:
            from autoskillit.server import mcp  # noqa: PLC0415

            for subset in ctx.config.subsets.disabled:
                mcp.disable(tags={subset})
        except ImportError:
            logger.error(
                "Could not import mcp for subset disable at startup"
                " — subset-disabled tools may be unexpectedly visible",
                exc_info=True,
            )

    # Wire MCP recording/replay middleware for scenario capture.
    # Core imports are outside the try/except so a broken autoskillit installation
    # surfaces as an error rather than being silently swallowed as a middleware warning.
    # The _mcp_middleware_registered flag on the runner prevents double-registration
    # if _initialize() is called more than once with the same runner instance.
    from autoskillit.execution import (  # noqa: PLC0415
        RecordingSubprocessRunner,
        ReplayingSubprocessRunner,
    )
    from autoskillit.server import mcp  # noqa: PLC0415

    if isinstance(ctx.runner, RecordingSubprocessRunner) and not getattr(
        ctx.runner, "_mcp_middleware_registered", False
    ):
        try:
            from api_simulator.mcp import McpRecordingMiddleware  # noqa: PLC0415

            mcp.add_middleware(McpRecordingMiddleware(ctx.runner.recorder))
            ctx.runner._mcp_middleware_registered = True  # type: ignore[attr-defined]
            logger.info("mcp_recording_middleware_registered")
        except ImportError:
            logger.warning("mcp_scenario_middleware_unavailable", exc_info=True)
        except Exception:
            logger.warning("mcp_scenario_middleware_registration_failed", exc_info=True)
    elif (
        isinstance(ctx.runner, ReplayingSubprocessRunner)
        and ctx.runner.player is not None
        and not getattr(ctx.runner, "_mcp_middleware_registered", False)
    ):
        try:
            from api_simulator.mcp import McpReplayMiddleware  # noqa: PLC0415

            mcp.add_middleware(McpReplayMiddleware(ctx.runner.player))
            ctx.runner._mcp_middleware_registered = True  # type: ignore[attr-defined]
            logger.info("mcp_replay_middleware_registered")
        except ImportError:
            logger.warning("mcp_scenario_middleware_unavailable", exc_info=True)
        except Exception:
            logger.warning("mcp_scenario_middleware_registration_failed", exc_info=True)


async def deferred_initialize(ctx: ToolContext, *, ready_event: asyncio.Event) -> None:
    """Run deferrable startup I/O as a background task.

    Called from _autoskillit_lifespan after mcp.run() opens the transport.
    Sets ready_event when complete — tools needing audit data await this event.
    """
    try:
        from autoskillit.execution import recover_crashed_sessions  # noqa: PLC0415

        cfg = ctx.config.linux_tracing
        n = recover_crashed_sessions(
            tmpfs_path=cfg.tmpfs_path,
            log_dir=cfg.log_dir,
        )
        if n > 0:
            logger.info("Recovered %d crashed session trace(s) from tmpfs", n)
    except Exception:
        logger.debug("recover_crashed_sessions at startup failed", exc_info=True)

    try:
        from datetime import datetime, timedelta  # noqa: PLC0415

        from autoskillit.execution import (  # noqa: PLC0415
            read_telemetry_clear_marker,
            resolve_log_dir,
        )

        cfg = ctx.config.linux_tracing
        log_root = resolve_log_dir(cfg.log_dir)
        since_dt = datetime.now(tz=UTC) - timedelta(hours=24)
        clear_marker = read_telemetry_clear_marker(log_root)
        if clear_marker is not None and clear_marker > since_dt:
            since_dt = clear_marker
        since_str = since_dt.isoformat()

        n_aud = ctx.audit.load_from_log_dir(log_root, since=since_str)

        if n_aud:
            logger.info(
                "Recovered telemetry from session logs (audit=%d)",
                n_aud,
            )
    except Exception:
        logger.warning("telemetry_recovery_at_startup_failed", exc_info=True)

    if ctx.session_skill_manager is not None:
        try:
            removed = ctx.session_skill_manager.cleanup_stale()
            if removed:
                logger.info("session_skill_cleanup", extra={"removed": removed})
        except Exception:
            logger.warning("session_skill_cleanup_failed", exc_info=True)

    ready_event.set()
    logger.info("deferred_initialize_complete")


def _get_ctx() -> ToolContext:
    """Return the active ToolContext. Raises if _initialize() has not been called."""
    if _ctx is None:
        raise RuntimeError(
            "serve() must be called before accessing context. "
            "Call server._initialize(ctx) before mcp.run()."
        )
    return _ctx


def _get_ctx_or_none() -> ToolContext | None:
    """Return the active ToolContext, or None if uninitialized.

    For ungated tools only. Gated tools must use _get_ctx() which raises.
    """
    return _ctx


def _get_config() -> AutomationConfig:
    """Return the active AutomationConfig from the ToolContext."""
    return _get_ctx().config


def _get_plugin_dir() -> str | None:
    """Return plugin_dir from the current server context, or None if uninitialized."""
    return _ctx.plugin_dir if _ctx is not None else None


def version_info() -> dict:
    """Return version health information for the running server."""
    from autoskillit.version import version_info as _compute_version

    plugin_dir = _ctx.plugin_dir if _ctx is not None else None
    return _compute_version(plugin_dir)
