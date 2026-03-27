"""MCP tool name prefix detection for CLI prompt builders.

Detects whether autoskillit is marketplace-installed or running under
direct --plugin-dir only, and derives the correct fully-qualified MCP
tool name prefix. Detection is pure Python I/O — no LLM, no subprocess,
no network calls.
"""

from __future__ import annotations

import json
from pathlib import Path

# The key written to installed_plugins.json by `autoskillit install`
_AUTOSKILLIT_PLUGIN_KEY = "autoskillit@autoskillit-local"

# Single source of truth for both known prefix forms
DIRECT_PREFIX = "mcp__autoskillit__"
MARKETPLACE_PREFIX = "mcp__plugin_autoskillit_autoskillit__"


def _installed_plugins_path() -> Path:
    """Return the path to Claude Code's installed plugins registry."""
    return Path.home() / ".claude" / "plugins" / "installed_plugins.json"


def detect_autoskillit_mcp_prefix() -> str:
    """Return the MCP prefix that autoskillit tools will use in a spawned session.

    Reads ~/.claude/plugins/installed_plugins.json to determine whether
    autoskillit is marketplace-installed. When it is, the marketplace
    prefix takes precedence even when --plugin-dir is also passed.

    Falls back to DIRECT_PREFIX if the file is absent, unreadable, or
    does not contain the autoskillit key.
    """
    try:
        data = json.loads(_installed_plugins_path().read_text())
        if _AUTOSKILLIT_PLUGIN_KEY in data.get("plugins", {}):
            return MARKETPLACE_PREFIX
    except (OSError, json.JSONDecodeError, AttributeError):
        pass
    return DIRECT_PREFIX


def resolve_tool_name(short_name: str, prefix: str | None = None) -> str:
    """Return the fully-qualified MCP tool name for the given short name.

    If prefix is None, detect_autoskillit_mcp_prefix() is called. Pass
    prefix explicitly when it has already been resolved for a session to
    avoid re-reading the filesystem.
    """
    if prefix is None:
        prefix = detect_autoskillit_mcp_prefix()
    return f"{prefix}{short_name}"
