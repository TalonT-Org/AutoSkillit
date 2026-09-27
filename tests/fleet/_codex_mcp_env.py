"""Shared Codex MCP-server environment construction for fleet tests.

Codex builds an MCP stdio server's environment from scratch (``env_clear()``)
as ``DEFAULT_ENV_VARS`` union config.toml's ``mcp_servers.<name>.env_vars`` —
never the launching process's full ambient environment. Provenance for the
default set: Codex ``rust-v0.144.0``
``codex-rs/rmcp-client/src/utils.rs::DEFAULT_ENV_VARS`` (Unix list).
"""

from __future__ import annotations

import tomllib
from collections.abc import Iterable, Mapping
from pathlib import Path

CODEX_MCP_DEFAULT_ENV_VARS: tuple[str, ...] = (
    "HOME",
    "LOGNAME",
    "PATH",
    "SHELL",
    "USER",
    "__CF_USER_TEXT_ENCODING",
    "LANG",
    "LC_ALL",
    "TERM",
    "TMPDIR",
    "TZ",
)


def codex_mcp_server_env(parent_env: Mapping[str, str], env_vars: Iterable[str]) -> dict[str, str]:
    """Return the env Codex builds for an MCP stdio server it launches.

    Mirrors Codex's own construction: ``DEFAULT_ENV_VARS`` union *env_vars*,
    each name resolved against *parent_env* (the Codex process's own
    environment) and dropped if absent there.
    """
    return {
        key: parent_env[key]
        for key in (*CODEX_MCP_DEFAULT_ENV_VARS, *env_vars)
        if key in parent_env
    }


def codex_mcp_autoskillit_env_vars(codex_home: Path) -> list[str]:
    """Return ``mcp_servers.autoskillit.env_vars`` from a Codex home's config.toml."""
    with (codex_home / "config.toml").open("rb") as config_file:
        config = tomllib.load(config_file)
    return list(config["mcp_servers"]["autoskillit"]["env_vars"])
