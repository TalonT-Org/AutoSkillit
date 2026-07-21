from __future__ import annotations

from autoskillit.core import CodingAgentBackend

from ._codex_config import (
    CODEX_AUTO_COMPACT_LIMIT,
    CODEX_LIMITS_LAST_VERIFIED_VERSION,
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
from ._composite_locator import CompositeSessionLocator
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


def resolve_worst_case_delivery_bound() -> int:
    """Smallest ``effective_delivery_token_limit`` across all registered backends.

    Acts as the canonical "worst-case default" used when backend capabilities
    are unavailable or zero. Returns ``0`` only if every registered backend
    reports zero; production callers normalize that case at the enforcement
    boundary rather than here.
    """
    limits: list[int] = []
    for backend_cls in BACKEND_REGISTRY.values():
        caps = backend_cls().capabilities
        limit = getattr(caps, "effective_delivery_token_limit", 0)
        if limit > 0:
            limits.append(limit)
    return min(limits) if limits else 0


__all__ = [
    "BACKEND_REGISTRY",
    "CODEX_EXEC_FLAGS",
    "CODEX_TOP_LEVEL_ONLY_FLAGS",
    "CompositeSessionLocator",
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
    "CODEX_LIMITS_LAST_VERIFIED_VERSION",
    "CODEX_AUTO_COMPACT_LIMIT",
    "NON_VARIADIC_CODEX_FLAGS",
    "VARIADIC_CODEX_FLAGS",
    "_is_autoskillit_registered",
    "_read_codex_config",
    "_serialize_toml",
    "_write_codex_config",
    "ensure_codex_mcp_registered",
    "get_backend",
    "make_codex_scenario_player",
    "resolve_worst_case_delivery_bound",
]
