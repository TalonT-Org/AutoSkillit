from __future__ import annotations

import pytest

from autoskillit.core import (
    CodingAgentBackend,
    EnvPolicy,
    ResultParser,
    SessionLocator,
    StreamParser,
)
from autoskillit.execution.backends import (
    BACKEND_REGISTRY,
    ClaudeCodeBackend,
    ClaudeEnvPolicy,
    ClaudeResultParser,
    ClaudeSessionLocator,
    ClaudeStreamParser,
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

    def test_all_classes_importable(self) -> None:
        assert isinstance(ClaudeCodeBackend(), CodingAgentBackend)
        assert isinstance(ClaudeEnvPolicy(), EnvPolicy)
        assert isinstance(ClaudeResultParser(), ResultParser)
        assert isinstance(ClaudeSessionLocator(), SessionLocator)
        assert isinstance(ClaudeStreamParser(), StreamParser)
