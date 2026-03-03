"""Mutable singleton state and context accessor functions for the MCP server.

Extracted from server/__init__.py to keep __init__.py a pure re-export facade
and to give tool modules a stable, non-circular import target for the raw _ctx
sentinel.

This module is the authoritative location for:
  - _ctx: the module-level ToolContext singleton
  - _initialize(ctx): called by cli/app.py serve() before mcp.run()
  - _get_ctx(): raises RuntimeError if uninitialized (used by gated tools)
  - _get_config(): convenience shortcut to _get_ctx().config
  - version_info(): public server version health query
"""

from __future__ import annotations

from autoskillit.config import AutomationConfig
from autoskillit.core import get_logger
from autoskillit.pipeline import ToolContext

logger = get_logger(__name__)

_ctx: ToolContext | None = None


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


def _get_plugin_dir() -> str | None:
    """Return plugin_dir from the current server context, or None if uninitialized."""
    return _ctx.plugin_dir if _ctx is not None else None


def version_info() -> dict:
    """Return version health information for the running server."""
    from autoskillit.version import version_info as _compute_version

    plugin_dir = _ctx.plugin_dir if _ctx is not None else None
    return _compute_version(plugin_dir)
