"""Cross-backend model translation contract and integration tests."""

from __future__ import annotations

import pytest

from autoskillit.core import SkillSessionConfig
from autoskillit.execution.backends.claude import ClaudeCodeBackend
from autoskillit.execution.backends.codex import CodexBackend

pytestmark = [pytest.mark.layer("execution"), pytest.mark.small]


class TestCodexTranslateModel:
    def test_rejects_claude_aliases(self) -> None:
        result = CodexBackend().translate_model("sonnet")
        assert result != "sonnet"
        assert result == "o4-mini"

    def test_strips_context_suffix(self) -> None:
        result = CodexBackend().translate_model("opus[1m]")
        assert "[1m]" not in result
        assert result == "o3"

    def test_passthrough_native(self) -> None:
        assert CodexBackend().translate_model("o3") == "o3"

    def test_unknown_passthrough(self) -> None:
        assert CodexBackend().translate_model("custom-model-xyz") == "custom-model-xyz"

    def test_haiku_alias(self) -> None:
        assert CodexBackend().translate_model("haiku") == "gpt-4o-mini"


class TestClaudeTranslateModel:
    def test_identity(self) -> None:
        assert ClaudeCodeBackend().translate_model("sonnet") == "sonnet"

    def test_strips_context_suffix(self) -> None:
        assert ClaudeCodeBackend().translate_model("opus[1m]") == "opus"

    def test_unknown_passthrough(self) -> None:
        assert ClaudeCodeBackend().translate_model("custom-model-xyz") == "custom-model-xyz"

    def test_haiku_identity(self) -> None:
        assert ClaudeCodeBackend().translate_model("haiku") == "haiku"

    def test_suffix_case_insensitive(self) -> None:
        assert ClaudeCodeBackend().translate_model("opus[1M]") == "opus"


class TestCodexBuildCmdTranslatesModel:
    def test_build_skill_session_cmd(self) -> None:
        config = SkillSessionConfig(model="sonnet", completion_marker="%%DONE%%")
        spec = CodexBackend().build_skill_session_cmd("/test", "/repo", config)
        model_idx = list(spec.cmd).index("--model")
        assert spec.cmd[model_idx + 1] == "o4-mini"

    def test_build_food_truck_cmd(self) -> None:
        from autoskillit.core import DirectInstall

        spec = CodexBackend().build_food_truck_cmd(
            orchestrator_prompt="test",
            plugin_source=DirectInstall(plugin_dir="/tmp/plugin"),
            cwd="/repo",
            completion_marker="%%DONE%%",
            model="sonnet",
        )
        model_idx = list(spec.cmd).index("--model")
        assert spec.cmd[model_idx + 1] == "o4-mini"

    def test_build_headless_cmd(self) -> None:
        spec = CodexBackend().build_headless_cmd("test prompt", model="sonnet")
        model_idx = list(spec.cmd).index("--model")
        assert spec.cmd[model_idx + 1] == "o4-mini"

    def test_build_interactive_cmd(self) -> None:
        spec = CodexBackend().build_interactive_cmd(model="sonnet")
        model_idx = list(spec.cmd).index("--model")
        assert spec.cmd[model_idx + 1] == "o4-mini"


class TestClaudeBuildCmdTranslatesModel:
    def test_build_headless_cmd_strips_suffix(self) -> None:
        spec = ClaudeCodeBackend().build_headless_cmd("test prompt", model="opus[1m]")
        model_idx = list(spec.cmd).index("--model")
        assert spec.cmd[model_idx + 1] == "opus"

    def test_build_interactive_cmd_strips_suffix(self) -> None:
        spec = ClaudeCodeBackend().build_interactive_cmd(model="sonnet[1m]")
        model_idx = list(spec.cmd).index("--model")
        assert spec.cmd[model_idx + 1] == "sonnet"

    def test_build_skill_session_cmd_strips_suffix(self) -> None:
        config = SkillSessionConfig(model="sonnet[1m]", completion_marker="%%DONE%%")
        spec = ClaudeCodeBackend().build_skill_session_cmd("/test", "/repo", config)
        model_idx = list(spec.cmd).index("--model")
        assert spec.cmd[model_idx + 1] == "sonnet"

    def test_build_food_truck_cmd_strips_suffix(self) -> None:
        from autoskillit.core import DirectInstall

        spec = ClaudeCodeBackend().build_food_truck_cmd(
            orchestrator_prompt="test",
            plugin_source=DirectInstall(plugin_dir="/tmp/plugin"),
            cwd="/repo",
            completion_marker="%%DONE%%",
            model="opus[1m]",
        )
        model_idx = list(spec.cmd).index("--model")
        assert spec.cmd[model_idx + 1] == "opus"
