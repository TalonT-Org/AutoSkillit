from __future__ import annotations

from autoskillit.core import CodingAgentBackend

from ._codex_config import (
    CODEX_MCP_REQUIRED_KEYS,
    CODEX_MCP_STARTUP_TIMEOUT_SEC,
    CODEX_MCP_TOOL_TIMEOUT_FLOOR,
    CODEX_TOOL_OUTPUT_TOKEN_LIMIT,
    _is_autoskillit_registered,
    _read_codex_config,
    _serialize_toml,
    _write_codex_config,
    ensure_codex_mcp_registered,
)
from ._codex_hooks import (
    _is_autoskillit_hook_entry,
    generate_codex_hooks_config,
    sync_hooks_to_codex_config,
)
from ._codex_parse import CodexResultParser, CodexStreamParser
from .claude import (
    ClaudeCodeBackend,
    ClaudeEnvPolicy,
    ClaudeResultParser,
    ClaudeSessionLocator,
    ClaudeStreamParser,
)
from .codex import (
    CODEX_EXEC_FLAGS,
    CODEX_TOP_LEVEL_ONLY_FLAGS,
    NON_VARIADIC_CODEX_FLAGS,
    VARIADIC_CODEX_FLAGS,
    CodexBackend,
    CodexEnvPolicy,
    CodexFlags,
    CodexSessionLocator,
)
from .codex_scenario_player import (
    CodexScenarioPlayer,
    make_codex_scenario_player,
)

BACKEND_REGISTRY: dict[str, type[CodingAgentBackend]] = {
    "claude-code": ClaudeCodeBackend,
    "codex": CodexBackend,
}


def get_backend(name: str) -> CodingAgentBackend:
    try:
        cls = BACKEND_REGISTRY[name]
    except KeyError:
        valid = ", ".join(sorted(BACKEND_REGISTRY))
        msg = f"Unknown backend {name!r}. Valid names: {valid}"
        raise ValueError(msg) from None
    return cls()


__all__ = [
    "BACKEND_REGISTRY",
    "CODEX_EXEC_FLAGS",
    "CODEX_TOP_LEVEL_ONLY_FLAGS",
    "_is_autoskillit_hook_entry",
    "generate_codex_hooks_config",
    "sync_hooks_to_codex_config",
    "ClaudeCodeBackend",
    "ClaudeEnvPolicy",
    "ClaudeResultParser",
    "ClaudeSessionLocator",
    "ClaudeStreamParser",
    "CodexBackend",
    "CodexEnvPolicy",
    "CodexFlags",
    "CodexResultParser",
    "CodexScenarioPlayer",
    "CodexSessionLocator",
    "CodexStreamParser",
    "CODEX_MCP_STARTUP_TIMEOUT_SEC",
    "CODEX_MCP_TOOL_TIMEOUT_FLOOR",
    "CODEX_MCP_REQUIRED_KEYS",
    "CODEX_TOOL_OUTPUT_TOKEN_LIMIT",
    "NON_VARIADIC_CODEX_FLAGS",
    "VARIADIC_CODEX_FLAGS",
    "_is_autoskillit_registered",
    "_read_codex_config",
    "_serialize_toml",
    "_write_codex_config",
    "ensure_codex_mcp_registered",
    "get_backend",
    "make_codex_scenario_player",
]
