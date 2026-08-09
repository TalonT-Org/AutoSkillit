"""MCP tool name prefix detection for the CLI.

Re-exports core detection primitives for CLI prompt builders.
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
]
