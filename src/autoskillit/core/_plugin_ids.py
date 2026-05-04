"""MCP tool name prefix detection — pure stdlib, importable from any layer.

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

# Cache subdirectory name used by all plugin cache path constructions
DIRECT_INSTALL_CACHE_SUBDIR = "autoskillit-local"

# Single source of truth for both known prefix forms
DIRECT_PREFIX = "mcp__autoskillit__"
MARKETPLACE_PREFIX = "mcp__plugin_autoskillit_autoskillit__"


def _installed_plugins_path() -> Path:
    """Return the path to Claude Code's installed plugins registry."""
    return Path.home() / ".claude" / "plugins" / "installed_plugins.json"


def _get_autoskillit_install_path() -> Path:
    """Return the installPath for autoskillit from installed_plugins.json.

    The plugin value can be a dict {"installPath": ...} (old format) or
    a list [{"installPath": ...}] (new scoped format). Raises KeyError if
    the plugin is not present; raises ValueError if the file is unreadable,
    unparseable, or the entry format is unexpected.
    """
    try:
        data = json.loads(_installed_plugins_path().read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"Cannot read installed_plugins.json: {exc}") from exc
    entry = data["plugins"][_AUTOSKILLIT_PLUGIN_KEY]
    if isinstance(entry, list):
        if not entry:
            raise ValueError(f"Empty install entry list for {_AUTOSKILLIT_PLUGIN_KEY!r}")
        entry = entry[0]
    install_path = entry.get("installPath") if isinstance(entry, dict) else None
    if install_path is None:
        raise ValueError(
            f"Missing 'installPath' in entry for {_AUTOSKILLIT_PLUGIN_KEY!r}: {entry!r}"
        )
    return Path(install_path)


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
    except (OSError, json.JSONDecodeError, AttributeError, TypeError):
        pass
    return DIRECT_PREFIX
