from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from autoskillit.core import EnvPolicy
from autoskillit.execution.backends import ClaudeEnvPolicy

pytestmark = [pytest.mark.layer("execution"), pytest.mark.small]


class TestClaudeEnvPolicy:
    def test_build_env_delegates_to_build_agent_env(self) -> None:
        result = ClaudeEnvPolicy().build_env({})
        assert "CLAUDE_CODE_AUTO_CONNECT_IDE" in result

    def test_build_env_returns_dict(self) -> None:
        result = ClaudeEnvPolicy().build_env({})
        assert type(result) is dict

    def test_build_env_strips_ide_vars(self) -> None:
        base_env = {
            "CLAUDE_CODE_SSE_PORT": "9876",
            "ENABLE_IDE_INTEGRATION": "1",
            "EDITOR": "vim",
        }
        result = ClaudeEnvPolicy().build_env(base_env)
        assert "CLAUDE_CODE_SSE_PORT" not in result
        assert "ENABLE_IDE_INTEGRATION" not in result
        assert "EDITOR" in result

    def test_structural_conformance_env_policy(self) -> None:
        assert isinstance(ClaudeEnvPolicy(), EnvPolicy)

    def test_frozen(self) -> None:
        policy = ClaudeEnvPolicy()
        with pytest.raises((FrozenInstanceError, TypeError)):
            policy.some_attr = "value"  # type: ignore[misc]

    def test_build_env_forwards_extras(self) -> None:
        result = ClaudeEnvPolicy().build_env({}, extras={"FOO": "bar"})
        assert result.get("FOO") == "bar"

    def test_build_env_raises_on_missing_required(self) -> None:
        with pytest.raises(ValueError, match="MISSING_KEY"):
            ClaudeEnvPolicy().build_env({}, required=frozenset({"MISSING_KEY"}))
