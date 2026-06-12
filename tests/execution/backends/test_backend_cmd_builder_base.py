"""Tests for execution/backends/_backend_cmd_builder_base.py."""

from __future__ import annotations

import pytest

from autoskillit.core import (
    CAMPAIGN_ID_ENV_VAR,
    KITCHEN_SESSION_ID_ENV_VAR,
    SkillSessionConfig,
)
from autoskillit.execution.backends import ClaudeCodeBackend, CodexBackend
from autoskillit.execution.backends._backend_cmd_builder_base import (
    SHARED_BASELINE_ENV,
    BackendCmdBuilderBase,
    FlagVocabulary,
)

pytestmark = [pytest.mark.layer("execution"), pytest.mark.small]


class _ConcreteBuilder(BackendCmdBuilderBase):
    """Minimal concrete subclass for testing _apply_config."""

    def _binary(self) -> str:
        return "test-binary"

    def _sandbox_default(self) -> str:
        return "test-sandbox"

    def _env_policy(self):  # pragma: no cover - not exercised in these tests
        return None

    def _flag_vocabulary(self) -> FlagVocabulary:
        return FlagVocabulary(
            variadic_flags=frozenset(),
            non_variadic_flags=frozenset(),
            model_flag="--model",
            add_dir_flag="--add-dir",
            resume_flag="--resume",
            config_override_flag="-c",
        )


class TestFlagVocabulary:
    def test_fields(self) -> None:
        fv = FlagVocabulary(
            variadic_flags=frozenset({"--add-dir"}),
            non_variadic_flags=frozenset({"--json"}),
            model_flag="--model",
            add_dir_flag="--add-dir",
            resume_flag="--resume",
            config_override_flag="-c",
        )
        assert isinstance(fv.variadic_flags, frozenset)
        assert isinstance(fv.non_variadic_flags, frozenset)
        assert isinstance(fv.model_flag, str)
        assert isinstance(fv.add_dir_flag, str)
        assert isinstance(fv.resume_flag, str)
        assert isinstance(fv.config_override_flag, str)

    def test_is_namedtuple(self) -> None:
        assert issubclass(FlagVocabulary, tuple)
        assert hasattr(FlagVocabulary, "_fields")
        assert len(FlagVocabulary._fields) == 6


class TestAssembleSharedEnvExtras:
    @pytest.fixture(autouse=True)
    def _clean_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(CAMPAIGN_ID_ENV_VAR, "camp-123")
        monkeypatch.setenv(KITCHEN_SESSION_ID_ENV_VAR, "kitchen-456")

    def test_all_eight_keys(self) -> None:
        result = BackendCmdBuilderBase._assemble_shared_env_extras(
            write_prefix="/tmp/wp",
            write_prefixes=("/tmp/a", "/tmp/b"),
            cwd="/work",
            scenario_step_name="step1",
        )
        assert result["MAX_MCP_OUTPUT_TOKENS"] == "50000"
        assert result["MCP_CONNECTION_NONBLOCKING"] == "0"
        assert result[CAMPAIGN_ID_ENV_VAR] == "camp-123"
        assert result[KITCHEN_SESSION_ID_ENV_VAR] == "kitchen-456"
        assert result["AUTOSKILLIT_ALLOWED_WRITE_PREFIX"] == "/tmp/wp"
        assert result["AUTOSKILLIT_ALLOWED_WRITE_PREFIXES"] == "/tmp/a:/tmp/b"
        assert result["AUTOSKILLIT_CWD"] == "/work"
        assert result["SCENARIO_STEP_NAME"] == "step1"

    def test_conditional_keys_absent(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv(CAMPAIGN_ID_ENV_VAR, raising=False)
        monkeypatch.delenv(KITCHEN_SESSION_ID_ENV_VAR, raising=False)
        result = BackendCmdBuilderBase._assemble_shared_env_extras()
        assert "MAX_MCP_OUTPUT_TOKENS" in result
        assert "MCP_CONNECTION_NONBLOCKING" in result
        assert CAMPAIGN_ID_ENV_VAR not in result
        assert KITCHEN_SESSION_ID_ENV_VAR not in result
        assert "AUTOSKILLIT_ALLOWED_WRITE_PREFIX" not in result
        assert "AUTOSKILLIT_ALLOWED_WRITE_PREFIXES" not in result
        assert "AUTOSKILLIT_CWD" not in result
        assert "SCENARIO_STEP_NAME" not in result


class TestApplyConfig:
    def test_round_trip(self) -> None:
        config = SkillSessionConfig(
            completion_marker="%%DONE%%",
            model="sonnet",
            scenario_step_name="s1",
            allowed_write_prefix="/tmp",
            allowed_write_prefixes=("/a", "/b"),
            sandbox_mode="read-only",
        )
        result = _ConcreteBuilder()._apply_config(config)
        assert result["completion_marker"] == "%%DONE%%"
        assert result["model"] == "sonnet"
        assert result["scenario_step_name"] == "s1"
        assert result["allowed_write_prefix"] == "/tmp"
        assert result["allowed_write_prefixes"] == ("/a", "/b")
        assert result["sandbox_mode"] == "read-only"


class TestSubclassRelationship:
    def test_claude_is_subclass(self) -> None:
        assert isinstance(ClaudeCodeBackend(), BackendCmdBuilderBase)

    def test_codex_is_subclass(self) -> None:
        assert isinstance(CodexBackend(), BackendCmdBuilderBase)


class TestExtensionPoints:
    def test_claude_binary(self) -> None:
        assert ClaudeCodeBackend()._binary() == "claude"

    def test_codex_binary(self) -> None:
        assert CodexBackend()._binary() == "codex"

    def test_claude_flag_vocabulary(self) -> None:
        fv = ClaudeCodeBackend()._flag_vocabulary()
        assert isinstance(fv, FlagVocabulary)
        assert fv.model_flag == "--model"

    def test_codex_flag_vocabulary(self) -> None:
        fv = CodexBackend()._flag_vocabulary()
        assert isinstance(fv, FlagVocabulary)
        assert fv.model_flag == "--model"
        assert fv.config_override_flag == "-c"


class TestSharedBaselineEnv:
    def test_exactly_two_keys(self) -> None:
        assert set(SHARED_BASELINE_ENV.keys()) == {
            "MAX_MCP_OUTPUT_TOKENS",
            "MCP_CONNECTION_NONBLOCKING",
        }
