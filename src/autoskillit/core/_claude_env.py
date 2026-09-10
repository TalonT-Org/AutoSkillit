"""Backward-compat shim for _claude_env. Real module at autoskillit.core.claude_env.claude_env."""

from autoskillit.core.claude_env.claude_env import (
    IDE_ENV_ALWAYS_EXTRAS,
    IDE_ENV_DENYLIST,
    IDE_ENV_PREFIX_DENYLIST,
    build_agent_env,
    build_claude_env,
    build_maintenance_env,
    os,
    resolve_dbus_session_bus_address,
)

__all__ = [
    "IDE_ENV_ALWAYS_EXTRAS",
    "IDE_ENV_DENYLIST",
    "IDE_ENV_PREFIX_DENYLIST",
    "build_agent_env",
    "build_claude_env",
    "build_maintenance_env",
    "os",
    "resolve_dbus_session_bus_address",
]
