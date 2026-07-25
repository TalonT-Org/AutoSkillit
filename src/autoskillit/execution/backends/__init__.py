from __future__ import annotations

from autoskillit.core import CodingAgentBackend

from ._codex_config import (
    CODEX_AUTO_COMPACT_LIMIT,
    CODEX_HISTORY_RETENTION_TOKEN_LIMIT,
    CODEX_LIMITS_LAST_VERIFIED_VERSION,
    CODEX_MCP_REQUIRED_KEYS,
    CODEX_MCP_STARTUP_TIMEOUT_SEC,
    CODEX_MCP_TOOL_TIMEOUT_FLOOR,
    CODEX_RECIPE_DELIVERY_BUDGET,
    CODEX_RECIPE_DELIVERY_CALLING_CONTRACT,
    CODEX_RECIPE_DELIVERY_CALLING_CONTRACT_DIGEST,
    SUPPORTED_CODEX_RECIPE_EVIDENCE_REGISTRY,
    _is_autoskillit_registered,
    _read_codex_config,
    _serialize_toml,
    _write_codex_config,
    codex_recipe_delivery_calling_contract,
    ensure_codex_mcp_registered,
)
from ._codex_hooks import (
    _is_autoskillit_hook_entry,
    generate_codex_hooks_config,
    sync_hooks_to_codex_config,
)
from ._codex_parse import CodexResultParser, CodexStreamParser
from ._codex_recipe_delivery import (
    CodexAttestationResult,
    CodexHostCorrelation,
    CodexOuterBudgetAttestor,
    NullProtectedHostAttestationProvider,
    ProtectedHostAttestationProvider,
    ProtectedStoreAuthority,
    RecipeDeliveryReceiptLedger,
    RecipeReceiptHandle,
    RecipeReservationResult,
    enumerate_fresh_codex_marker_ids,
    read_rollout_thread_id,
    resolve_unique_codex_host_correlation,
)
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
    CodexStateReadinessProbe,
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
    "CodexAttestationResult",
    "CodexHostCorrelation",
    "CodexOuterBudgetAttestor",
    "CodexScenarioPlayer",
    "CodexSessionLocator",
    "CodexStateReadinessProbe",
    "CodexStreamParser",
    "CODEX_MCP_STARTUP_TIMEOUT_SEC",
    "CODEX_MCP_TOOL_TIMEOUT_FLOOR",
    "CODEX_MCP_REQUIRED_KEYS",
    "CODEX_HISTORY_RETENTION_TOKEN_LIMIT",
    "CODEX_RECIPE_DELIVERY_BUDGET",
    "CODEX_RECIPE_DELIVERY_CALLING_CONTRACT",
    "CODEX_RECIPE_DELIVERY_CALLING_CONTRACT_DIGEST",
    "SUPPORTED_CODEX_RECIPE_EVIDENCE_REGISTRY",
    "CODEX_LIMITS_LAST_VERIFIED_VERSION",
    "CODEX_AUTO_COMPACT_LIMIT",
    "NON_VARIADIC_CODEX_FLAGS",
    "NullProtectedHostAttestationProvider",
    "ProtectedHostAttestationProvider",
    "ProtectedStoreAuthority",
    "RecipeDeliveryReceiptLedger",
    "RecipeReceiptHandle",
    "RecipeReservationResult",
    "VARIADIC_CODEX_FLAGS",
    "_is_autoskillit_registered",
    "_read_codex_config",
    "_serialize_toml",
    "_write_codex_config",
    "codex_recipe_delivery_calling_contract",
    "ensure_codex_mcp_registered",
    "enumerate_fresh_codex_marker_ids",
    "get_backend",
    "make_codex_scenario_player",
    "read_rollout_thread_id",
    "resolve_unique_codex_host_correlation",
]
