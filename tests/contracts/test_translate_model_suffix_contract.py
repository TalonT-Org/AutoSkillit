"""Contract: translate_model suffix preservation tied to backend capability."""

from __future__ import annotations

import pytest

from autoskillit.execution.backends import BACKEND_REGISTRY
from autoskillit.execution.backends.claude import ClaudeCodeBackend

pytestmark = [pytest.mark.layer("contracts"), pytest.mark.small]

_SUFFIX_MODEL = "opus[1m]"


class TestSuffixCapabilityContract:
    def test_claude_code_has_suffix_capability(self) -> None:
        assert ClaudeCodeBackend().capabilities.supports_context_window_suffix is True

    @pytest.mark.parametrize("name", sorted(BACKEND_REGISTRY))
    def test_translate_model_suffix_consistent_with_capability(self, name: str) -> None:
        backend = BACKEND_REGISTRY[name]()
        result = backend.translate_model(_SUFFIX_MODEL)
        if backend.capabilities.supports_context_window_suffix:
            assert "[1m]" in result, (
                f"{name}: suffix must be preserved when supports_context_window_suffix=True"
            )
        else:
            assert "[1m]" not in result, (
                f"{name}: suffix must be stripped when supports_context_window_suffix=False"
            )

    def test_at_least_one_backend_supports_suffix(self) -> None:
        count = sum(
            1
            for cls in BACKEND_REGISTRY.values()
            if cls().capabilities.supports_context_window_suffix
        )
        assert count >= 1


class TestBuildHeadlessCmdSuffixContract:
    def test_build_headless_cmd_preserves_suffix(self) -> None:
        spec = ClaudeCodeBackend().build_headless_cmd("test", model=_SUFFIX_MODEL)
        model_idx = list(spec.cmd).index("--model")
        assert "[1m]" in spec.cmd[model_idx + 1]
