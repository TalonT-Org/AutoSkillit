"""Tests for backends/__init__.py — BACKEND_REGISTRY and get_backend()."""

from __future__ import annotations

import pytest

from autoskillit.execution.backends import (
    BACKEND_REGISTRY,
    ClaudeCodeBackend,
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

    def test_all_five_classes_importable(self) -> None:
        from autoskillit.execution.backends import (
            ClaudeEnvPolicy,
            ClaudeResultParser,
            ClaudeSessionLocator,
            ClaudeStreamParser,
        )

        assert all(
            callable(cls)
            for cls in (
                ClaudeCodeBackend,
                ClaudeEnvPolicy,
                ClaudeResultParser,
                ClaudeSessionLocator,
                ClaudeStreamParser,
            )
        )
