"""Shared-principal Codex explorer MCP projection validation and rendering."""

from __future__ import annotations

import tomllib
from collections.abc import Mapping
from pathlib import Path

from autoskillit.core import AgentDef
from autoskillit.execution.backends._codex_config import _serialize_toml

_EXPLORER_ROLE_NAMES = frozenset({"semantic-code-navigator", "repository-impact-profiler"})
_EXPLORATION_TOOL_PREFIX = "mcp__autoskillit__"
_EXPLORER_BROKER_TOOLS = (
    "submit_exploration_query",
    "get_exploration_page",
    "resume_exploration_context",
)
_SHARED_EXPLORER_PRINCIPAL = "shared-explorer-session"
_ROLE_MCP_TRANSPORT_KEYS = (
    "command",
    "url",
    "args",
    "env_vars",
    "startup_timeout_sec",
    "tool_timeout_sec",
)
_EXPLORER_BINDING_ENV_KEYS = frozenset(
    {
        "AUTOSKILLIT_EXPLORATION_CAPABILITY",
        "AUTOSKILLIT_EXPLORATION_ROLE",
        "AUTOSKILLIT_EXPLORATION_SESSION_ID",
        "AUTOSKILLIT_EXPLORATION_AUTHORITY_PATH",
    }
)


def _validated_explorer_binding_env(label: str, binding: Mapping[str, str]) -> dict[str, str]:
    """Copy one server-issued shared-principal binding after strict validation."""
    if not isinstance(binding, Mapping):
        raise ValueError(f"explorer binding for {label!r} must be an environment mapping")
    normalized = dict(binding)
    if set(normalized) != _EXPLORER_BINDING_ENV_KEYS:
        raise ValueError(
            f"explorer binding for {label!r} must contain exactly "
            f"{sorted(_EXPLORER_BINDING_ENV_KEYS)}"
        )
    if any(type(value) is not str or not value for value in normalized.values()):
        raise ValueError(f"explorer binding for {label!r} has an empty or non-text value")
    if normalized["AUTOSKILLIT_EXPLORATION_ROLE"] != _SHARED_EXPLORER_PRINCIPAL:
        raise ValueError(
            "explorer binding must identify the shared session principal, not a role-local "
            "authorization"
        )
    authority_path = Path(normalized["AUTOSKILLIT_EXPLORATION_AUTHORITY_PATH"])
    if not authority_path.is_absolute():
        raise ValueError(f"explorer binding authority path for {label!r} must be absolute")
    return normalized


def _validated_explorer_binding_envs(
    definitions: tuple[AgentDef, ...],
    explorer_binding_env: Mapping[str, Mapping[str, str]] | None,
) -> dict[str, dict[str, str]]:
    """Return the server-issued binding environment for each canonical explorer role.

    The role-keyed input is retained as a caller/backend interface, but its two
    values must be identical: Codex starts the parent MCP connection before it
    selects role metadata, so all three config layers represent one session
    principal.  The backend never creates those values; it only validates and
    copies the server-issued launch map.
    """
    if explorer_binding_env is None:
        return {}
    if not isinstance(explorer_binding_env, Mapping):
        raise ValueError("explorer binding environment must be a role mapping")
    if any(type(role) is not str for role in explorer_binding_env):
        raise ValueError("explorer binding environment roles must be text")

    definitions_by_name = {definition.name: definition for definition in definitions}
    defined_roles = set(definitions_by_name) & _EXPLORER_ROLE_NAMES
    if defined_roles != _EXPLORER_ROLE_NAMES:
        raise ValueError("explorer binding requires both canonical explorer roles")
    binding_roles = set(explorer_binding_env)
    if binding_roles != defined_roles:
        raise ValueError(
            "explorer binding projection must cover exactly the generated explorer roles: "
            f"expected={sorted(defined_roles)}, actual={sorted(binding_roles)}"
        )

    validated: dict[str, dict[str, str]] = {}
    shared_binding: dict[str, str] | None = None
    for role in sorted(binding_roles):
        definition = definitions_by_name[role]
        projected_tools = tuple(
            tool.removeprefix(_EXPLORATION_TOOL_PREFIX)
            for tool in definition.tools
            if tool.startswith(_EXPLORATION_TOOL_PREFIX)
        )
        if len(projected_tools) != len(definition.tools) or projected_tools != (
            _EXPLORER_BROKER_TOOLS
        ):
            raise ValueError(
                f"explorer role {role!r} must project exactly the exploration broker tools"
            )
        if definition.codex.sandbox_mode != "read-only":
            raise ValueError(f"explorer role {role!r} must be read-only")

        binding = _validated_explorer_binding_env(role, explorer_binding_env[role])
        if shared_binding is None:
            shared_binding = binding
        elif binding != shared_binding:
            raise ValueError(
                "explorer bindings must be identical for the shared session principal"
            )
        validated[role] = binding
    return validated


def _canonical_explorer_mcp_transport(config_path: Path) -> dict[str, object]:
    """Copy the canonical parent transport without copying enabled state or secrets."""
    try:
        config = tomllib.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise ValueError(f"invalid Codex MCP configuration: {exc}") from exc
    servers = config.get("mcp_servers")
    server = servers.get("autoskillit") if isinstance(servers, dict) else None
    if not isinstance(server, dict):
        raise ValueError("explorer binding requires a canonical autoskillit MCP server")

    command = server.get("command")
    url = server.get("url")
    valid_command = isinstance(command, str) and bool(command.strip())
    valid_url = isinstance(url, str) and bool(url.strip())
    if valid_command == valid_url:
        raise ValueError(
            "explorer binding requires exactly one canonical autoskillit MCP transport"
        )

    transport: dict[str, object] = {}
    for key in _ROLE_MCP_TRANSPORT_KEYS:
        value = server.get(key)
        if value is not None:
            transport[key] = value
    for key in ("args", "env_vars"):
        value = transport.get(key)
        if value is not None and (
            not isinstance(value, list) or any(not isinstance(item, str) for item in value)
        ):
            raise ValueError(f"canonical autoskillit MCP {key} must be a text list")
    for key in ("startup_timeout_sec", "tool_timeout_sec"):
        value = transport.get(key)
        if value is not None and (isinstance(value, bool) or not isinstance(value, (int, float))):
            raise ValueError(f"canonical autoskillit MCP {key} must be numeric")
    return transport


def _explorer_mcp_projection(
    explorer_mcp_transport: Mapping[str, object],
    explorer_binding_env: Mapping[str, str] | None,
) -> dict[str, object]:
    """Return the exact MCP projection shared by the parent and both roles."""
    projection = {
        key: explorer_mcp_transport[key]
        for key in _ROLE_MCP_TRANSPORT_KEYS
        if key in explorer_mcp_transport
    }
    projection["enabled"] = True
    projection["enabled_tools"] = list(_EXPLORER_BROKER_TOOLS)
    if explorer_binding_env is not None:
        projection["env"] = {
            key: explorer_binding_env[key] for key in sorted(_EXPLORER_BINDING_ENV_KEYS)
        }
    return projection


def _render_parent_explorer_config(
    config_text: str,
    *,
    explorer_mcp_transport: Mapping[str, object],
    explorer_binding_env: Mapping[str, str] | None,
) -> str:
    """Render a read-only parent with the same exact MCP principal as its roles."""
    try:
        config = tomllib.loads(config_text)
    except tomllib.TOMLDecodeError as exc:
        raise ValueError(f"invalid Codex MCP configuration: {exc}") from exc
    servers = config.get("mcp_servers")
    if not isinstance(servers, dict):
        raise ValueError("explorer binding requires a canonical autoskillit MCP server")
    config["sandbox_mode"] = "read-only"
    config["mcp_servers"] = {
        "autoskillit": _explorer_mcp_projection(
            explorer_mcp_transport,
            explorer_binding_env,
        )
    }
    rendered = _serialize_toml(config)
    rendered_config = tomllib.loads(rendered)
    rendered_servers = rendered_config.get("mcp_servers")
    if not isinstance(rendered_servers, dict) or set(rendered_servers) != {"autoskillit"}:
        raise ValueError("explorer parent must configure exactly the autoskillit MCP server")
    return rendered
