"""Tests for ``_backend_cmd_builder_base``.

Covers the ``BackendCmdBuilderBase`` ABC and ``FlagVocabulary`` NamedTuple.
"""

from __future__ import annotations

import pytest

from autoskillit.core import (
    SkillSessionConfig,
)
from autoskillit.execution.backends._backend_cmd_builder_base import (
    BackendCmdBuilderBase,
    FlagVocabulary,
)
from autoskillit.execution.backends.claude import ClaudeCodeBackend
from autoskillit.execution.backends.codex import CodexBackend

pytestmark = [pytest.mark.layer("execution"), pytest.mark.small]


class TestFlagVocabulary:
    def test_field_names(self) -> None:
        assert FlagVocabulary._fields == (
            "variadic_flags",
            "non_variadic_flags",
            "model_flag",
            "add_dir_flag",
            "resume_flag",
            "config_override_flag",
        )

    def test_field_types_via_instance(self) -> None:
        fv = FlagVocabulary(
            variadic_flags=frozenset({"--add-dir"}),
            non_variadic_flags=frozenset({"--model"}),
            model_flag="--model",
            add_dir_flag="--add-dir",
            resume_flag="--resume",
            config_override_flag="",
        )
        assert isinstance(fv.variadic_flags, frozenset)
        assert isinstance(fv.non_variadic_flags, frozenset)
        assert isinstance(fv.model_flag, str)
        assert isinstance(fv.add_dir_flag, str)
        assert isinstance(fv.resume_flag, str)
        assert isinstance(fv.config_override_flag, str)


class TestAssembleSharedEnvExtras:
    def test_all_eight_keys_present(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("AUTOSKILLIT_CAMPAIGN_ID", "camp-1")
        monkeypatch.setenv("AUTOSKILLIT_KITCHEN_SESSION_ID", "kitchen-1")
        backend = ClaudeCodeBackend()
        result = backend._assemble_shared_env_extras(
            scenario_step_name="step-1",
            allowed_write_prefix="/tmp/write",
            allowed_write_prefixes=("/tmp/a", "/tmp/b"),
            cwd="/workspace",
        )
        assert result["MAX_MCP_OUTPUT_TOKENS"] == "50000"
        assert result["MCP_CONNECTION_NONBLOCKING"] == "0"
        assert result["AUTOSKILLIT_CAMPAIGN_ID"] == "camp-1"
        assert result["AUTOSKILLIT_KITCHEN_SESSION_ID"] == "kitchen-1"
        assert result["AUTOSKILLIT_ALLOWED_WRITE_PREFIX"] == "/tmp/write"
        assert result["AUTOSKILLIT_ALLOWED_WRITE_PREFIXES"] == "/tmp/a:/tmp/b"
        assert result["AUTOSKILLIT_CWD"] == "/workspace"
        assert result["SCENARIO_STEP_NAME"] == "step-1"

    def test_conditional_keys_absent_when_falsy(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("AUTOSKILLIT_CAMPAIGN_ID", raising=False)
        monkeypatch.delenv("AUTOSKILLIT_KITCHEN_SESSION_ID", raising=False)
        backend = ClaudeCodeBackend()
        result = backend._assemble_shared_env_extras()
        assert "AUTOSKILLIT_CAMPAIGN_ID" not in result
        assert "AUTOSKILLIT_KITCHEN_SESSION_ID" not in result
        assert "AUTOSKILLIT_ALLOWED_WRITE_PREFIX" not in result
        assert "AUTOSKILLIT_ALLOWED_WRITE_PREFIXES" not in result
        assert "AUTOSKILLIT_CWD" not in result
        assert "SCENARIO_STEP_NAME" not in result
        # Unconditional keys always present
        assert "MAX_MCP_OUTPUT_TOKENS" in result
        assert "MCP_CONNECTION_NONBLOCKING" in result

    def test_session_type_is_not_assembled(self) -> None:
        """AUTOSKILLIT_SESSION_TYPE is a backend-specific concern and must
        not appear in the shared extras output."""
        backend = ClaudeCodeBackend()
        result = backend._assemble_shared_env_extras(
            scenario_step_name="step-1",
            allowed_write_prefix="/p",
            allowed_write_prefixes=("/a", "/b"),
            cwd="/w",
        )
        assert "AUTOSKILLIT_SESSION_TYPE" not in result

    def test_codex_backend_uses_same_assembly(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("AUTOSKILLIT_CAMPAIGN_ID", "camp-2")
        backend = CodexBackend()
        result = backend._assemble_shared_env_extras(
            scenario_step_name="step-2",
            allowed_write_prefix="/p2",
            allowed_write_prefixes=("/c", "/d"),
            cwd="/w2",
        )
        assert result["MAX_MCP_OUTPUT_TOKENS"] == "50000"
        assert result["MCP_CONNECTION_NONBLOCKING"] == "0"
        assert result["AUTOSKILLIT_CAMPAIGN_ID"] == "camp-2"
        assert result["SCENARIO_STEP_NAME"] == "step-2"
        assert result["AUTOSKILLIT_ALLOWED_WRITE_PREFIX"] == "/p2"
        assert result["AUTOSKILLIT_ALLOWED_WRITE_PREFIXES"] == "/c:/d"
        assert result["AUTOSKILLIT_CWD"] == "/w2"


class TestApplyConfig:
    def test_round_trip(self) -> None:
        config = SkillSessionConfig(
            completion_marker="DONE",
            model="sonnet",
            scenario_step_name="step-x",
            allowed_write_prefix="/tmp/w",
            sandbox_mode="read-only",
        )
        backend = ClaudeCodeBackend()
        result = backend._apply_config(config)
        assert result["completion_marker"] == "DONE"
        assert result["model"] == "sonnet"
        assert result["scenario_step_name"] == "step-x"
        assert result["allowed_write_prefix"] == "/tmp/w"
        assert result["sandbox_mode"] == "read-only"

    def test_all_eighteen_fields_present(self) -> None:
        config = SkillSessionConfig()
        backend = CodexBackend()
        result = backend._apply_config(config)
        expected_fields = {
            "completion_marker",
            "model",
            "plugin_source",
            "output_format",
            "add_dirs",
            "exit_after_stop_delay_ms",
            "stream_idle_timeout_ms",
            "scenario_step_name",
            "temp_dir_relpath",
            "allowed_write_prefix",
            "allowed_write_prefixes",
            "provider_extras",
            "profile_name",
            "resume_session_id",
            "resume_checkpoint",
            "resume_message",
            "sandbox_mode",
            "backend_override",
        }
        assert set(result.keys()) == expected_fields
        assert len(result) == 18


class TestInheritance:
    def test_claude_is_subclass(self) -> None:
        assert isinstance(ClaudeCodeBackend(), BackendCmdBuilderBase)

    def test_codex_is_subclass(self) -> None:
        assert isinstance(CodexBackend(), BackendCmdBuilderBase)


class TestExtensionPoints:
    def test_claude_binary(self) -> None:
        assert ClaudeCodeBackend()._binary == "claude"

    def test_codex_binary(self) -> None:
        assert CodexBackend()._binary == "codex"

    def test_claude_sandbox_default(self) -> None:
        assert ClaudeCodeBackend()._sandbox_default == ""

    def test_codex_sandbox_default(self) -> None:
        assert CodexBackend()._sandbox_default == "workspace-write"

    def test_claude_flag_vocabulary(self) -> None:
        fv = ClaudeCodeBackend()._flag_vocabulary
        assert "--model" in fv.non_variadic_flags
        assert "--add-dir" in fv.variadic_flags
        assert fv.model_flag == "--model"
        assert fv.add_dir_flag == "--add-dir"
        assert fv.resume_flag == "--resume"
        assert fv.config_override_flag == ""

    def test_codex_flag_vocabulary(self) -> None:
        fv = CodexBackend()._flag_vocabulary
        assert "-c" in fv.variadic_flags
        assert fv.config_override_flag == "-c"
        assert fv.model_flag == "--model"
        assert fv.add_dir_flag == "--add-dir"
