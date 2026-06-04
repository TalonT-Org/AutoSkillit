from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from autoskillit.core import AGENT_BACKEND_ENV_VAR, AUTOSKILLIT_PRIVATE_ENV_VARS, EnvPolicy
from autoskillit.execution.backends.codex import CodexEnvPolicy

pytestmark = [pytest.mark.layer("execution"), pytest.mark.small]


class TestCodexEnvPolicy:
    def test_anthropic_credentials_stripped(self) -> None:
        policy = CodexEnvPolicy()
        base = {
            "ANTHROPIC_API_KEY": "sk-ant-secret",
            "ANTHROPIC_AUTH_TOKEN": "token-secret",
            "ANTHROPIC_BASE_URL": "https://api.anthropic.com",
            "PATH": "/usr/bin",
        }
        result = policy.build_env(base)
        assert "ANTHROPIC_API_KEY" not in result
        assert "ANTHROPIC_AUTH_TOKEN" not in result
        assert "ANTHROPIC_BASE_URL" not in result
        assert result["PATH"] == "/usr/bin"

    def test_claude_code_prefix_vars_stripped(self) -> None:
        policy = CodexEnvPolicy()
        base = {
            "CLAUDE_CODE_SSE_PORT": "9876",
            "CLAUDE_CODE_IDE_FOO": "bar",
            "CLAUDE_CODE_AUTO_CONNECT_IDE": "0",
            "HOME": "/home/user",
        }
        result = policy.build_env(base)
        assert "CLAUDE_CODE_SSE_PORT" not in result
        assert "CLAUDE_CODE_IDE_FOO" not in result
        assert "CLAUDE_CODE_AUTO_CONNECT_IDE" not in result
        assert result["HOME"] == "/home/user"

    def test_autoskillit_private_vars_stripped(self) -> None:
        policy = CodexEnvPolicy()
        base: dict[str, str] = {var: "sentinel" for var in AUTOSKILLIT_PRIVATE_ENV_VARS}
        base["PATH"] = "/usr/bin"
        result = policy.build_env(base)
        for var in AUTOSKILLIT_PRIVATE_ENV_VARS:
            assert var not in result
        assert result["PATH"] == "/usr/bin"

    def test_claude_code_auto_connect_ide_not_injected(self) -> None:
        policy = CodexEnvPolicy()
        result = policy.build_env({"PATH": "/usr/bin"})
        assert "CLAUDE_CODE_AUTO_CONNECT_IDE" not in result

    def test_openai_api_key_preserved(self) -> None:
        policy = CodexEnvPolicy()
        base = {"OPENAI_API_KEY": "sk-openai-secret", "PATH": "/usr/bin"}
        result = policy.build_env(base)
        assert result["OPENAI_API_KEY"] == "sk-openai-secret"

    def test_codex_api_key_preserved(self) -> None:
        policy = CodexEnvPolicy()
        base = {"CODEX_API_KEY": "sk-codex-secret", "PATH": "/usr/bin"}
        result = policy.build_env(base)
        assert result["CODEX_API_KEY"] == "sk-codex-secret"

    def test_extras_overlay_applied_after_scrub(self) -> None:
        policy = CodexEnvPolicy()
        base = {"PATH": "/usr/bin"}
        result = policy.build_env(base, extras={"CUSTOM_VAR": "custom_value"})
        assert result["CUSTOM_VAR"] == "custom_value"

    def test_extras_trusted_channel_bypasses_denylist(self) -> None:
        # extras is a trusted-injection channel: callers can explicitly re-introduce
        # denied vars (e.g. to forward credentials to a trusted subprocess).
        policy = CodexEnvPolicy()
        base = {"ANTHROPIC_API_KEY": "sk-from-base", "PATH": "/usr/bin"}
        result = policy.build_env(base, extras={"ANTHROPIC_API_KEY": "sk-trusted-override"})
        assert result["ANTHROPIC_API_KEY"] == "sk-trusted-override"

    def test_required_sentinel_raises_value_error(self) -> None:
        policy = CodexEnvPolicy()
        with pytest.raises(ValueError, match="MISSING_VAR"):
            policy.build_env({"PATH": "/usr/bin"}, required=frozenset({"MISSING_VAR"}))

    def test_isinstance_env_policy_protocol(self) -> None:
        assert isinstance(CodexEnvPolicy(), EnvPolicy)

    def test_frozen(self) -> None:
        # CodexEnvPolicy has no dataclass fields; any attribute assignment on a
        # frozen+slots dataclass raises TypeError (CPython) or FrozenInstanceError
        # depending on the Python version and slots interaction.
        policy = CodexEnvPolicy()
        with pytest.raises((FrozenInstanceError, TypeError, AttributeError)):
            policy.some_attr = "value"  # type: ignore[misc]

    def test_agent_backend_stripped_from_base(self) -> None:
        policy = CodexEnvPolicy()
        base = {"AUTOSKILLIT_AGENT_BACKEND": "codex", "PATH": "/usr/bin"}
        result = policy.build_env(base)
        assert "AUTOSKILLIT_AGENT_BACKEND" not in result

    def test_agent_backend_passes_through_extras(self) -> None:
        policy = CodexEnvPolicy()
        base = {"PATH": "/usr/bin"}
        result = policy.build_env(base, extras={"AUTOSKILLIT_AGENT_BACKEND": "codex"})
        assert result["AUTOSKILLIT_AGENT_BACKEND"] == "codex"

    def test_agent_backend_in_private_env_vars(self) -> None:
        assert AGENT_BACKEND_ENV_VAR in AUTOSKILLIT_PRIVATE_ENV_VARS
