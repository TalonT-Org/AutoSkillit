"""AutoSkillit plugin identifiers and MCP tool-name authorities — pure stdlib.

Holds the plugin registry keys, the Claude Code plugin tool-naming rule, and
the prefix AutoSkillit's MCP tools carry inside a session it launches. That
prefix follows the launch corridor (how the backend loads AutoSkillit), never
host registry state. Importable from any layer; no LLM, subprocess, or
network calls.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..types import BackendCapabilities

# The key written to installed_plugins.json by `autoskillit install`
_AUTOSKILLIT_PLUGIN_KEY = "autoskillit@autoskillit-local"

# Namespaces AutoSkillit's own Python-package install-root generation store,
# disjoint from _AUTOSKILLIT_PLUGIN_KEY's projected-plugin generation tree.
_AUTOSKILLIT_INSTALL_ROOT_KEY = "autoskillit-install@autoskillit-local"

# Cache subdirectory name used by all plugin cache path constructions
DIRECT_INSTALL_CACHE_SUBDIR = "autoskillit-local"

# Single source of truth for both known prefix forms
DIRECT_PREFIX = "mcp__autoskillit__"
# Prefix Claude Code assigns to AutoSkillit's MCP tools in every plugin-loaded
# session (--plugin-dir and marketplace alike):
# mcp__plugin_<plugin.json name>_<.mcp.json server key>__
PLUGIN_PREFIX = "mcp__plugin_autoskillit_autoskillit__"

_CLAUDE_TOOL_SEGMENT_INVALID_CHARS = re.compile(r"[^A-Za-z0-9_-]")
_QUALIFIED_AUTOSKILLIT_TOOL_RE = re.compile(
    r"mcp__[A-Za-z0-9_-]*autoskillit[A-Za-z0-9_-]*__[A-Za-z0-9_]+"
)


def _claude_tool_segment(value: str) -> str:
    if not value:
        raise ValueError("Claude plugin tool-name segment must not be empty")
    return _CLAUDE_TOOL_SEGMENT_INVALID_CHARS.sub("_", value)


def claude_plugin_tool_prefix(plugin_name: str, server_key: str) -> str:
    """Return the prefix Claude Code assigns to a plugin-provided MCP server's tools.

    Claude Code MCP docs (https://code.claude.com/docs/en/mcp, plugin-provided
    servers): tools are named ``mcp__plugin_<plugin-name>_<server-name>__<tool-name>``,
    where any character outside ``A-Z``, ``a-z``, ``0-9``, ``_``, and ``-`` is
    replaced with ``_``. The rule does not vary by load mode: ``--plugin-dir`` and
    marketplace loading produce the same names.
    """
    return f"mcp__plugin_{_claude_tool_segment(plugin_name)}_{_claude_tool_segment(server_key)}__"


def _read_plugin_json_object(path: Path) -> dict[str, object]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ValueError(f"cannot read {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return data


def read_claude_plugin_tool_prefix(plugin_root: Path) -> str:
    """Derive the MCP tool prefix Claude Code registers for the plugin at *plugin_root*.

    Reads the plugin name from ``.claude-plugin/plugin.json`` and the single
    server key from ``.mcp.json``. Raises ``ValueError`` naming the offending
    file when either is missing, unreadable, or malformed.
    """
    plugin_json = plugin_root / ".claude-plugin" / "plugin.json"
    name = _read_plugin_json_object(plugin_json).get("name")
    if not isinstance(name, str) or not name:
        raise ValueError(f"{plugin_json} must declare a non-empty string 'name'")
    mcp_json = plugin_root / ".mcp.json"
    servers = _read_plugin_json_object(mcp_json).get("mcpServers")
    if not isinstance(servers, dict) or len(servers) != 1:
        raise ValueError(f"{mcp_json} must declare exactly one server under 'mcpServers'")
    (server_key,) = servers
    if not isinstance(server_key, str) or not server_key:
        raise ValueError(f"{mcp_json} 'mcpServers' key must be a non-empty string")
    return claude_plugin_tool_prefix(name, server_key)


def find_qualified_autoskillit_tool_names(text: str) -> tuple[str, ...]:
    """Return every namespace-qualified AutoSkillit MCP tool name in *text*."""
    return tuple(_QUALIFIED_AUTOSKILLIT_TOOL_RE.findall(text))


def _installed_plugins_path(home: Path | None = None) -> Path:
    """Return the path to Claude Code's installed plugins registry."""
    base = Path.home() if home is None else Path(home)
    return base / ".claude" / "plugins" / "installed_plugins.json"


def installed_plugin_semantic_key(plugin_ref: str, version: str) -> str:
    """Bind an installed artifact to one exact plugin/version transaction."""
    if not plugin_ref or not version:
        raise ValueError("installed plugin reference and version must not be empty")
    return f"{plugin_ref}:{version}"


def parse_installed_plugin_semantic_key(semantic_key: str) -> tuple[str, str]:
    """Return the plugin reference and version encoded in a semantic key."""
    plugin_ref, separator, version = semantic_key.rpartition(":")
    if not separator or not plugin_ref or not version:
        raise ValueError(f"invalid installed plugin semantic key: {semantic_key!r}")
    return plugin_ref, version


def registered_install_paths(home: Path | None = None) -> tuple[Path, ...]:
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
        data = json.loads(_installed_plugins_path(home).read_text())
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


def launched_session_mcp_prefix(capabilities: BackendCapabilities) -> str:
    """Prefix of AutoSkillit's MCP tools inside a session AutoSkillit launches with this backend.

    Claude children always load AutoSkillit as a plugin (``--plugin-dir``);
    Codex children register ``[mcp_servers.autoskillit]``. Pure: never reads
    host state.
    """
    return PLUGIN_PREFIX if capabilities.claude_plugin_tool_namespace else DIRECT_PREFIX


def is_marketplace_plugin_registered(home: Path | None = None) -> bool:
    """Host-level registry presence, for diagnostics about the *running* session's hook
    source only; never a tool-name authority.

    Checks key presence only and never dereferences ``installPath``. Never
    raises: an absent, unreadable, or malformed registry reads as unregistered.
    """
    try:
        data = json.loads(_installed_plugins_path(home).read_text())
    except (OSError, ValueError):
        return False
    if not isinstance(data, dict):
        return False
    plugins = data.get("plugins")
    return isinstance(plugins, dict) and _AUTOSKILLIT_PLUGIN_KEY in plugins


def validate_agent_tool_canonical(tool: str) -> str:
    """Assert a tool string is in DIRECT-canonical form and return its short name.

    Raises ValueError if the tool does not start with DIRECT_PREFIX or its short
    name is neither a canonical exploration tool nor a registered inspection tool.
    """
    if not tool.startswith(DIRECT_PREFIX):
        raise ValueError(
            f"agent tool {tool!r} must use the direct-install canonical prefix {DIRECT_PREFIX!r}"
        )
    return validate_agent_tool_short_name(tool[len(DIRECT_PREFIX) :])


def validate_agent_tool_short_name(short: str) -> str:
    """Assert *short* names an agent-admissible AutoSkillit tool and return it.

    Raises ValueError unless *short* is a canonical exploration tool or a
    registered inspection tool.
    """
    from ..tool_registry import get_tool_def
    from ..types import EXPLORATION_TOOLS, ToolInitializationOperation

    if short not in EXPLORATION_TOOLS:
        try:
            tool_def = get_tool_def(short)
        except KeyError:
            tool_def = None
        if (
            tool_def is None
            or tool_def.initialization_operation is not ToolInitializationOperation.INSPECTION
        ):
            raise ValueError(
                f"agent tool short name {short!r} is not a registered exploration tool "
                "or allowed inspection tool"
            )
    return short


def project_agent_tool_name(tool: str, target_prefix: str) -> str:
    """Project a DIRECT-canonical agent tool name to a target prefix form.

    Validates that *tool* is in authored (DIRECT_PREFIX) form, then replaces
    the prefix with *target_prefix*.  Non-MCP tools (those without the
    ``mcp__`` prefix) are returned unchanged.
    """
    if not tool.startswith("mcp__"):
        return tool
    short = validate_agent_tool_canonical(tool)
    return f"{target_prefix}{short}"
