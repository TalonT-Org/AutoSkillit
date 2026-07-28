"""MCP tool name prefix detection and resolution for the CLI.

Re-exports core detection primitives and provides resolve_tool_name for
constructing fully-qualified tool names within CLI prompt builders.
"""

from __future__ import annotations

from autoskillit.core import (
    DIRECT_PREFIX,
    MARKETPLACE_PREFIX,
    detect_autoskillit_mcp_prefix,
)

__all__ = [
    "DIRECT_PREFIX",
    "MARKETPLACE_PREFIX",
    "detect_autoskillit_mcp_prefix",
    "resolve_tool_name",
]


def resolve_tool_name(short_name: str, prefix: str) -> str:
    """Return a tool name using the prefix already resolved for this backend."""
    return f"{prefix}{short_name}"
