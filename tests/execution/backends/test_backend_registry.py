from __future__ import annotations

import pytest

from autoskillit.execution.backends import (
    BACKEND_REGISTRY,
    ClaudeCodeBackend,
    CodexBackend,
    get_backend,
)

pytestmark = [pytest.mark.layer("execution"), pytest.mark.small]


class TestBackendRegistry:
    def test_registry_is_the_only_semantic_adapter_authority(self) -> None:
        from autoskillit.core import SkillSemanticPlan

        for backend_name, backend_cls in BACKEND_REGISTRY.items():
            backend = backend_cls()
            result = backend.adapt_skill_semantics(SkillSemanticPlan(schema_version=1))
            assert result.unsupported_operation is None, backend_name

    def test_get_backend_claude_code(self) -> None:
        result = get_backend("claude-code")
        assert isinstance(result, ClaudeCodeBackend)

    def test_get_backend_unknown_raises_value_error(self) -> None:
        with pytest.raises(ValueError) as exc_info:
            get_backend("unknown-backend")
        assert "claude-code" in str(exc_info.value)

    def test_registry_has_claude_code_key(self) -> None:
        assert "claude-code" in BACKEND_REGISTRY

    def test_backend_registry_value_type(self) -> None:
        assert BACKEND_REGISTRY["claude-code"] is ClaudeCodeBackend

    def test_registry_has_codex_key(self) -> None:
        assert "codex" in BACKEND_REGISTRY

    def test_get_backend_codex(self) -> None:
        result = get_backend("codex")
        assert isinstance(result, CodexBackend)

    def test_all_exports_complete(self) -> None:
        from autoskillit.execution.backends import __all__ as all_exports

        expected = {
            "BACKEND_REGISTRY",
            "CODEX_AUTO_COMPACT_LIMIT",
            "CODEX_EXEC_FLAGS",
            "CODEX_LIMITS_LAST_VERIFIED_VERSION",
            "CODEX_MCP_REQUIRED_KEYS",
            "CODEX_MCP_STARTUP_TIMEOUT_SEC",
            "CODEX_MCP_TOOL_TIMEOUT_FLOOR",
            "CODEX_HISTORY_RETENTION_TOKEN_LIMIT",
            "CODEX_TOP_LEVEL_ONLY_FLAGS",
            "CODEX_RECIPE_DELIVERY_BUDGET",
            "CODEX_RECIPE_DELIVERY_CALLING_CONTRACT",
            "CODEX_RECIPE_DELIVERY_CALLING_CONTRACT_DIGEST",
            "CompositeSessionLocator",
            "ClaudeCodeBackend",
            "ClaudeEnvPolicy",
            "ClaudeResultParser",
            "ClaudeSessionLocator",
            "ClaudeStreamParser",
            "CodexBackend",
            "CodexAttestationResult",
            "CodexHostCorrelation",
            "CodexOuterBudgetAttestor",
            "CodexEnvPolicy",
            "CodexFlags",
            "CodexResultParser",
            "CodexScenarioPlayer",
            "CodexSessionLocator",
            "CodexStateReadinessProbe",
            "CodexStreamParser",
            "NON_VARIADIC_CODEX_FLAGS",
            "NullProtectedHostAttestationProvider",
            "ProtectedHostAttestationProvider",
            "ProtectedStoreAuthority",
            "RecipeDeliveryReceiptLedger",
            "RecipeReceiptHandle",
            "RecipeReservationResult",
            "SUPPORTED_CODEX_RECIPE_EVIDENCE_REGISTRY",
            "VARIADIC_CODEX_FLAGS",
            "_is_autoskillit_hook_entry",
            "_is_autoskillit_registered",
            "_read_codex_config",
            "_serialize_toml",
            "_write_codex_config",
            "all_backends",
            "codex_recipe_delivery_calling_contract",
            "ensure_codex_mcp_registered",
            "extract_codex_execution_identity",
            "enumerate_fresh_codex_marker_ids",
            "generate_codex_hooks_config",
            "get_backend",
            "make_codex_scenario_player",
            "read_rollout_thread_id",
            "resolve_unique_codex_host_correlation",
            "sync_hooks_to_codex_config",
        }
        assert set(all_exports) == expected
