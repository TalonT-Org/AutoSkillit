"""Split integrity tests for execution/backends/ split.

Verifies that new modules are importable and the public API surface is preserved.
"""

import pytest

pytestmark = [pytest.mark.layer("execution"), pytest.mark.small]


class TestClaudePromptModuleExists:
    """Symbols moved to _claude_prompt are importable from there."""

    def test__ensure_skill_prefix_importable(self):
        from autoskillit.execution.backends._claude_prompt import _ensure_skill_prefix

        assert callable(_ensure_skill_prefix)

    def test__inject_completion_directive_importable(self):
        from autoskillit.execution.backends._claude_prompt import _inject_completion_directive

        assert callable(_inject_completion_directive)

    def test__MAX_MCP_OUTPUT_TOKENS_VALUE_importable(self):
        from autoskillit.execution.backends._claude_prompt import _MAX_MCP_OUTPUT_TOKENS_VALUE

        assert _MAX_MCP_OUTPUT_TOKENS_VALUE == "50000"


class TestCodexConfigModuleExists:
    """Symbols moved to _codex_config are importable from there."""

    def test__ensure_codex_mcp_registered_importable(self):
        from autoskillit.execution.backends._codex_config import ensure_codex_mcp_registered

        assert callable(ensure_codex_mcp_registered)

    def test__serialize_toml_importable(self):
        from autoskillit.execution.backends._codex_config import _serialize_toml

        assert callable(_serialize_toml)


class TestCodexParseModuleExists:
    """Symbols moved to _codex_parse are importable from there."""

    def test__CodexStreamParser_importable(self):
        from autoskillit.execution.backends._codex_parse import CodexStreamParser

        assert CodexStreamParser is not None

    def test__CodexResultParser_importable(self):
        from autoskillit.execution.backends._codex_parse import CodexResultParser

        assert CodexResultParser is not None

    def test__scan_codex_ndjson_importable(self):
        from autoskillit.execution.backends._codex_parse import _scan_codex_ndjson

        assert callable(_scan_codex_ndjson)


class TestBackendsPublicAPISurfacePreserved:
    """All __all__ symbols from backends/__init__ are importable."""

    def test_claude_backend_importable(self):
        from autoskillit.execution.backends import ClaudeCodeBackend

        assert ClaudeCodeBackend is not None

    def test_codex_backend_importable(self):
        from autoskillit.execution.backends import CodexBackend

        assert CodexBackend is not None

    def test_ensure_codex_mcp_registered_importable(self):
        from autoskillit.execution.backends import ensure_codex_mcp_registered

        assert callable(ensure_codex_mcp_registered)

    def test_get_backend_importable(self):
        from autoskillit.execution.backends import get_backend

        assert callable(get_backend)
