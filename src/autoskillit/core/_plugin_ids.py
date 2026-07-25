"""MCP tool name prefix detection — pure stdlib, importable from any layer.

Detects whether autoskillit is marketplace-installed or running under
direct --plugin-dir only, and derives the correct fully-qualified MCP
tool name prefix. Detection is pure Python I/O — no LLM, no subprocess,
no network calls.
"""

from __future__ import annotations

import functools
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


def registered_install_paths() -> tuple[Path, ...]:
    """Return every ``installPath`` recorded for autoskillit, for diagnostics only.

    This is a *reporting* primitive, not a resolution one: no execution path may
    derive a plugin source from ``installed_plugins.json``. That file is written,
    versioned, and garbage-collected by Claude Code, so a path read from it can
    name a directory that no longer exists — which is exactly how the registry
    and the filesystem drift apart. ``verify_install_state()`` consumes this to
    *report* the drift; the projection resolves from ``pkg_root()`` instead.

    Lives in core/ (pure stdlib, importable from any layer) so IL-1 ``workspace``
    can read the registry without importing ``cli.InstalledPluginsFile`` (IL-3),
    which IL-005 forbids.

    Never raises: an absent, unreadable, or malformed file yields ``()``.
    """
    try:
        data = json.loads(_installed_plugins_path().read_text())
    except (OSError, json.JSONDecodeError):
        return ()
    if not isinstance(data, dict):
        return ()
    plugins = data.get("plugins")
    if not isinstance(plugins, dict):
        return ()
    entry = plugins.get(_AUTOSKILLIT_PLUGIN_KEY)
    entries = entry if isinstance(entry, list) else [entry]
    paths: list[Path] = []
    for item in entries:
        if not isinstance(item, dict):
            continue
        install_path = item.get("installPath")
        if isinstance(install_path, str) and install_path:
            paths.append(Path(install_path))
    return tuple(paths)


@functools.lru_cache(maxsize=1)
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
