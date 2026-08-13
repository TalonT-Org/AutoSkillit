"""Tests for execution/backends/_backend_cmd_builder_base.py."""

from __future__ import annotations

import pytest

from autoskillit.core import (
    AUTOSKILLIT_STATE_ROOT_ENV_VAR,
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

pytestmark = [pytest.mark.layer("execution"), pytest.mark.medium]


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
        assert fv.variadic_flags == frozenset({"--add-dir"})
        assert fv.non_variadic_flags == frozenset({"--json"})
        assert fv.model_flag == "--model"
        assert fv.add_dir_flag == "--add-dir"
        assert fv.resume_flag == "--resume"
        assert fv.config_override_flag == "-c"

    def test_is_namedtuple(self) -> None:
        assert issubclass(FlagVocabulary, tuple)
        assert hasattr(FlagVocabulary, "_fields")
        assert len(FlagVocabulary._fields) == 6


class TestAssembleSharedEnvExtras:
    @pytest.fixture(autouse=True)
    def _clean_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(CAMPAIGN_ID_ENV_VAR, "camp-123")
        monkeypatch.setenv(KITCHEN_SESSION_ID_ENV_VAR, "kitchen-456")

    def test_all_nine_keys(self) -> None:
        result = BackendCmdBuilderBase._assemble_shared_env_extras(
            session_type="skill",
            applicable_guards=frozenset({"write_guard"}),
            write_guard_tool_names=frozenset({"Write", "Edit"}),
            write_prefix="/tmp/wp",
            write_prefixes=("/tmp/a", "/tmp/b"),
            cwd="/work",
            scenario_step_name="step1",
        )
        assert len(result) == 13
        assert result["MAX_MCP_OUTPUT_TOKENS"] == "50000"
        assert result["MCP_CONNECTION_NONBLOCKING"] == "0"
        assert "AUTOSKILLIT_ATTESTED_CLIENT_GATE_TOKENS" not in result
        assert "AUTOSKILLIT_ATTESTED_META_SUPPORT" not in result
        assert result["AUTOSKILLIT_HEADLESS"] == "1"
        assert result["AUTOSKILLIT_SESSION_TYPE"] == "skill"
        assert result["AUTOSKILLIT_APPLICABLE_GUARDS"] == "write_guard"
        assert result["AUTOSKILLIT_WRITE_GUARD_TOOL_NAMES"] == "Edit,Write"
        assert result[CAMPAIGN_ID_ENV_VAR] == "camp-123"
        assert result[KITCHEN_SESSION_ID_ENV_VAR] == "kitchen-456"
        assert result["AUTOSKILLIT_ALLOWED_WRITE_PREFIX"] == "/tmp/wp"
        assert result["AUTOSKILLIT_ALLOWED_WRITE_PREFIXES"] == "/tmp/a:/tmp/b"
        assert result["AUTOSKILLIT_CWD"] == "/work"
        assert result[AUTOSKILLIT_STATE_ROOT_ENV_VAR] == "/work"
        assert result["SCENARIO_STEP_NAME"] == "step1"

    def test_conditional_keys_absent(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv(CAMPAIGN_ID_ENV_VAR, raising=False)
        monkeypatch.delenv(KITCHEN_SESSION_ID_ENV_VAR, raising=False)
        result = BackendCmdBuilderBase._assemble_shared_env_extras()
        assert "MAX_MCP_OUTPUT_TOKENS" in result
        assert "MCP_CONNECTION_NONBLOCKING" in result
        assert result["AUTOSKILLIT_HEADLESS"] == "1"
        assert CAMPAIGN_ID_ENV_VAR not in result
        assert KITCHEN_SESSION_ID_ENV_VAR not in result
        assert "AUTOSKILLIT_ALLOWED_WRITE_PREFIX" not in result
        assert "AUTOSKILLIT_ALLOWED_WRITE_PREFIXES" not in result
        assert "AUTOSKILLIT_CWD" not in result
        assert AUTOSKILLIT_STATE_ROOT_ENV_VAR not in result
        assert "SCENARIO_STEP_NAME" not in result
        assert "AUTOSKILLIT_SESSION_TYPE" not in result
        assert "AUTOSKILLIT_APPLICABLE_GUARDS" not in result
        assert "AUTOSKILLIT_WRITE_GUARD_TOOL_NAMES" not in result


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
        """Host client attestation is backend-specific (Claude-only) and must
        NOT live in SHARED_BASELINE_ENV — see claude.py's
        _CLAUDE_HOST_ATTESTATION_ENV. Advertising it to every backend would
        make Codex eligible to bypass its receipt-based protected delivery
        pipeline via the annotation-aware inline shortcut.
        """
        assert set(SHARED_BASELINE_ENV.keys()) == {
            "MAX_MCP_OUTPUT_TOKENS",
            "MCP_CONNECTION_NONBLOCKING",
        }


class TestMaxMcpOutputTokensCommentAccuracy:
    """The comment above _MAX_MCP_OUTPUT_TOKENS_VALUE must reflect the binary-verified
    (CLI 2.1.220) annotated/unannotated gating topology, not the earlier empirical
    ~100KB guess (issue #4253)."""

    def _preceding_comment(self) -> str:
        import ast
        from pathlib import Path

        path = (
            Path(__file__).resolve().parents[3]
            / "src"
            / "autoskillit"
            / "execution"
            / "backends"
            / "_backend_cmd_builder_base.py"
        )
        source = path.read_text()
        tree = ast.parse(source)
        target_lineno = None
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.AnnAssign)
                and isinstance(node.target, ast.Name)
                and node.target.id == "_MAX_MCP_OUTPUT_TOKENS_VALUE"
            ):
                target_lineno = node.lineno
                break
        assert target_lineno is not None, "_MAX_MCP_OUTPUT_TOKENS_VALUE not found in source"
        lines = source.splitlines()
        comment_lines: list[str] = []
        idx = target_lineno - 2  # 0-indexed line immediately above the assignment
        while idx >= 0 and lines[idx].strip().startswith("#"):
            comment_lines.insert(0, lines[idx].strip().lstrip("#").strip())
            idx -= 1
        return " ".join(comment_lines)

    def test_max_mcp_output_tokens_comment_accuracy(self) -> None:
        comment = self._preceding_comment()
        assert "inline token limit" in comment
        assert "Binary-verified" in comment
        assert "2.1.220" in comment
        assert "annotated" in comment
        assert "500,000" in comment
        assert "preventing open_kitchen() responses" not in comment
        assert "persisted to a file instead of returned inline" not in comment
