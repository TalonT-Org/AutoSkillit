"""Backward-compat shim for _plugin_ids — see core.plugins._plugin_ids."""

from autoskillit.core.plugins._plugin_ids import (
    _AUTOSKILLIT_INSTALL_ROOT_KEY,
    _AUTOSKILLIT_PLUGIN_KEY,
    DIRECT_INSTALL_CACHE_SUBDIR,
    DIRECT_PREFIX,
    MARKETPLACE_PREFIX,
    _installed_plugins_path,
    claude_plugin_tool_prefix,
    detect_autoskillit_mcp_prefix,
    find_qualified_autoskillit_tool_names,
    installed_plugin_semantic_key,
    parse_installed_plugin_semantic_key,
    project_agent_tool_name,
    read_claude_plugin_tool_prefix,
    registered_install_paths,
    validate_agent_tool_canonical,
    validate_agent_tool_short_name,
)

__all__ = [
    "DIRECT_INSTALL_CACHE_SUBDIR",
    "DIRECT_PREFIX",
    "MARKETPLACE_PREFIX",
    "_AUTOSKILLIT_INSTALL_ROOT_KEY",
    "_AUTOSKILLIT_PLUGIN_KEY",
    "_installed_plugins_path",
    "claude_plugin_tool_prefix",
    "detect_autoskillit_mcp_prefix",
    "find_qualified_autoskillit_tool_names",
    "installed_plugin_semantic_key",
    "parse_installed_plugin_semantic_key",
    "project_agent_tool_name",
    "read_claude_plugin_tool_prefix",
    "registered_install_paths",
    "validate_agent_tool_canonical",
    "validate_agent_tool_short_name",
]
