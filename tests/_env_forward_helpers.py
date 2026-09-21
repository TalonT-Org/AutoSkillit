"""Shared helpers for mcp_env_forward_vars assertions.

The Codex MCP env-forward vars set is defined once in
``autoskillit.core.types._type_constants_env`` as
``CODEX_MCP_ENV_FORWARD_VARS``. A handful of tests across the suite need to
subtract the always-explicit ``MANAGED_JOIN_PARENT_ID_ENV_VAR`` injection from
that set before asserting. Centralising the subtraction prevents each call
site from re-implementing the same set difference.
"""

from __future__ import annotations

from importlib import import_module
from typing import Any

from autoskillit.core import MANAGED_JOIN_PARENT_ID_ENV_VAR


def codex_mcp_env_forward_vars_minus_parent_id() -> frozenset[str]:
    """Return ``CODEX_MCP_ENV_FORWARD_VARS`` minus the explicit parent-id var."""
    constants = import_module("autoskillit.core.types._type_constants_env")
    return frozenset(constants.CODEX_MCP_ENV_FORWARD_VARS) - {MANAGED_JOIN_PARENT_ID_ENV_VAR}


def always_injected_forward_vars(backend: Any) -> frozenset[str]:
    """Exclude launch identities that must arrive through explicit extras."""
    return frozenset(backend.capabilities.mcp_env_forward_vars) - {MANAGED_JOIN_PARENT_ID_ENV_VAR}
