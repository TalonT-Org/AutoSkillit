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
            "CODEX_EXEC_FLAGS",
            "CODEX_MCP_STARTUP_TIMEOUT_SEC",
            "CODEX_MCP_TOOL_TIMEOUT_FLOOR",
            "CODEX_TOP_LEVEL_ONLY_FLAGS",
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
            "NON_VARIADIC_CODEX_FLAGS",
            "VARIADIC_CODEX_FLAGS",
            "_is_autoskillit_hook_entry",
            "_is_autoskillit_registered",
            "_read_codex_config",
            "_serialize_toml",
            "_write_codex_config",
            "ensure_codex_mcp_registered",
            "generate_codex_hooks_config",
            "get_backend",
            "make_codex_scenario_player",
            "sync_hooks_to_codex_config",
        }
        assert set(all_exports) == expected
