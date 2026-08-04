"""Cross-backend model translation contract and integration tests."""

from __future__ import annotations

import pytest

from autoskillit.core import SkillSessionConfig
from autoskillit.core.types._type_backend import CLAUDE_MODEL_ALIASES, CODEX_MODEL_ALIASES
from autoskillit.execution.backends.claude import ClaudeCodeBackend
from autoskillit.execution.backends.codex import CodexBackend
from tests.execution.backends._plugin_binding import plugin_binding

pytestmark = [pytest.mark.layer("execution"), pytest.mark.small]


class TestCodexTranslateModel:
    def test_rejects_claude_aliases(self) -> None:
        result = CodexBackend().translate_model("sonnet")
        assert result != "sonnet"
        assert result == CODEX_MODEL_ALIASES["sonnet"]

    def test_strips_context_suffix(self) -> None:
        result = CodexBackend().translate_model("opus[1m]")
        assert "[1m]" not in result
        assert result == CODEX_MODEL_ALIASES["opus"]

    @pytest.mark.parametrize("model_id", ["gpt-5.5", "gpt-5.6-sol"])
    def test_passthrough_native(self, model_id: str) -> None:
        assert CodexBackend().translate_model(model_id) == model_id

    def test_unknown_passthrough(self) -> None:
        assert CodexBackend().translate_model("custom-model-xyz") == "custom-model-xyz"

    def test_haiku_alias(self) -> None:
        assert CodexBackend().translate_model("haiku") == CODEX_MODEL_ALIASES["haiku"]


class TestClaudeTranslateModel:
    def test_sonnet_alias(self) -> None:
        assert ClaudeCodeBackend().translate_model("sonnet") == CLAUDE_MODEL_ALIASES["sonnet"]

    def test_preserves_context_suffix(self) -> None:
        assert ClaudeCodeBackend().translate_model("opus[1m]") == "opus[1m]"

    def test_unknown_passthrough(self) -> None:
        assert ClaudeCodeBackend().translate_model("custom-model-xyz") == "custom-model-xyz"

    def test_haiku_identity(self) -> None:
        assert ClaudeCodeBackend().translate_model("haiku") == "haiku"

    def test_suffix_case_insensitive(self) -> None:
        assert ClaudeCodeBackend().translate_model("opus[1M]") == "opus[1M]"


class TestCodexBuildCmdTranslatesModel:
    def test_build_skill_session_cmd(self) -> None:
        config = SkillSessionConfig(model="sonnet", completion_marker="%%DONE%%")
        spec = CodexBackend().build_skill_session_cmd("/test", "/repo", config)
        model_idx = list(spec.cmd).index("--model")
        assert spec.cmd[model_idx + 1] == CODEX_MODEL_ALIASES["sonnet"]

    def test_build_food_truck_cmd(self) -> None:
        spec = CodexBackend().build_food_truck_cmd(
            orchestrator_prompt="test",
            plugin_binding=plugin_binding("/tmp/plugin"),
            cwd="/repo",
            completion_marker="%%DONE%%",
            model="sonnet",
        )
        model_idx = list(spec.cmd).index("--model")
        assert spec.cmd[model_idx + 1] == CODEX_MODEL_ALIASES["sonnet"]

    def test_build_headless_cmd(self) -> None:
        spec = CodexBackend().build_headless_cmd("test prompt", model="sonnet")
        model_idx = list(spec.cmd).index("--model")
        assert spec.cmd[model_idx + 1] == CODEX_MODEL_ALIASES["sonnet"]

    def test_build_interactive_cmd(self) -> None:
        spec = CodexBackend().build_interactive_cmd(model="sonnet")
        model_idx = list(spec.cmd).index("--model")
        assert spec.cmd[model_idx + 1] == CODEX_MODEL_ALIASES["sonnet"]


class TestClaudeBuildCmdTranslatesModel:
    def test_build_headless_cmd_preserves_suffix(self) -> None:
        spec = ClaudeCodeBackend().build_headless_cmd("test prompt", model="opus[1m]")
        model_idx = list(spec.cmd).index("--model")
        assert spec.cmd[model_idx + 1] == "opus[1m]"

    def test_build_interactive_cmd_preserves_suffix(self) -> None:
        spec = ClaudeCodeBackend().build_interactive_cmd(model="sonnet[1m]")
        model_idx = list(spec.cmd).index("--model")
        assert spec.cmd[model_idx + 1] == f"{CLAUDE_MODEL_ALIASES['sonnet']}[1m]"

    def test_build_skill_session_cmd_preserves_suffix(self) -> None:
        config = SkillSessionConfig(model="sonnet[1m]", completion_marker="%%DONE%%")
        spec = ClaudeCodeBackend().build_skill_session_cmd("/test", "/repo", config)
        model_idx = list(spec.cmd).index("--model")
        assert spec.cmd[model_idx + 1] == f"{CLAUDE_MODEL_ALIASES['sonnet']}[1m]"

    def test_build_food_truck_cmd_preserves_suffix(self) -> None:
        from pathlib import Path

        spec = ClaudeCodeBackend().build_food_truck_cmd(
            orchestrator_prompt="test",
            plugin_binding=plugin_binding(Path("/tmp/plugin")),
            cwd="/repo",
            completion_marker="%%DONE%%",
            model="opus[1m]",
        )
        model_idx = list(spec.cmd).index("--model")
        assert spec.cmd[model_idx + 1] == "opus[1m]"


class TestCodexModelConfigOverrides:
    def test_returns_effort_for_sonnet_alias(self) -> None:
        overrides = CodexBackend().model_config_overrides("sonnet")
        assert "model_reasoning_effort=high" in overrides

    def test_returns_effort_for_opus_alias(self) -> None:
        overrides = CodexBackend().model_config_overrides("opus")
        assert "model_reasoning_effort=xhigh" in overrides

    def test_returns_effort_for_haiku_alias(self) -> None:
        overrides = CodexBackend().model_config_overrides("haiku")
        assert "model_reasoning_effort=medium" in overrides

    def test_strips_context_suffix_before_lookup(self) -> None:
        overrides = CodexBackend().model_config_overrides("opus[1m]")
        assert "model_reasoning_effort=xhigh" in overrides

    def test_no_effort_for_native_model(self) -> None:
        overrides = CodexBackend().model_config_overrides("gpt-5.5")
        assert not any("model_reasoning_effort" in o for o in overrides)


class TestClaudeModelConfigOverrides:
    def test_returns_empty_tuple(self) -> None:
        assert ClaudeCodeBackend().model_config_overrides("sonnet") == ()

    def test_returns_empty_tuple_for_any_model(self) -> None:
        assert ClaudeCodeBackend().model_config_overrides("opus") == ()


class TestCodexEffortInjectionInCmds:
    def test_headless_cmd_sonnet_has_effort_high(self) -> None:
        spec = CodexBackend().build_headless_cmd("test prompt", model="sonnet")
        cmd = list(spec.cmd)
        assert "-c" in cmd
        assert "model_reasoning_effort=high" in cmd

    def test_headless_cmd_opus_has_effort_xhigh(self) -> None:
        spec = CodexBackend().build_headless_cmd("test prompt", model="opus")
        cmd = list(spec.cmd)
        assert "model_reasoning_effort=xhigh" in cmd

    def test_headless_cmd_haiku_has_effort_medium(self) -> None:
        spec = CodexBackend().build_headless_cmd("test prompt", model="haiku")
        cmd = list(spec.cmd)
        assert "model_reasoning_effort=medium" in cmd

    def test_skill_session_cmd_has_effort(self) -> None:
        config = SkillSessionConfig(model="sonnet", completion_marker="%%DONE%%")
        spec = CodexBackend().build_skill_session_cmd("/test", "/repo", config)
        assert "model_reasoning_effort=high" in list(spec.cmd)

    def test_food_truck_cmd_has_effort(self) -> None:
        spec = CodexBackend().build_food_truck_cmd(
            orchestrator_prompt="test",
            plugin_binding=plugin_binding("/tmp/plugin"),
            cwd="/repo",
            completion_marker="%%DONE%%",
            model="sonnet",
        )
        assert "model_reasoning_effort=high" in list(spec.cmd)

    def test_food_truck_opus_suffix_uses_shared_model_with_xhigh_effort(self) -> None:
        spec = CodexBackend().build_food_truck_cmd(
            orchestrator_prompt="test",
            plugin_binding=plugin_binding("/tmp/plugin"),
            cwd="/repo",
            completion_marker="%%DONE%%",
            model="opus[1m]",
        )
        cmd = list(spec.cmd)
        model_idx = cmd.index("--model")
        assert cmd[model_idx + 1] == CODEX_MODEL_ALIASES["opus"]
        assert "[1m]" not in cmd[model_idx + 1]
        assert "model_reasoning_effort=xhigh" in cmd

    def test_interactive_cmd_has_effort(self) -> None:
        spec = CodexBackend().build_interactive_cmd(model="sonnet")
        assert "model_reasoning_effort=high" in list(spec.cmd)

    def test_no_effort_for_native_model_in_headless_cmd(self) -> None:
        spec = CodexBackend().build_headless_cmd("test prompt", model="gpt-5.5")
        assert "model_reasoning_effort" not in " ".join(spec.cmd)


class TestModelClass:
    def test_canonical_keys(self) -> None:
        from autoskillit.core import model_class

        assert model_class("opus") == "opus"
        assert model_class("sonnet") == "sonnet"
        assert model_class("haiku") == "haiku"

    def test_suffix_stripping(self) -> None:
        from autoskillit.core import model_class

        assert model_class("opus[1m]") == "opus"
        assert model_class("sonnet[1m]") == "sonnet"
        assert model_class("haiku[1m]") == "haiku"

    def test_codex_shared_model_id_stays_native_when_class_is_ambiguous(self) -> None:
        from autoskillit.core import model_class

        shared_model_id = CODEX_MODEL_ALIASES["opus"]
        assert {CODEX_MODEL_ALIASES[key] for key in ("sonnet", "opus", "haiku")} == {
            shared_model_id
        }
        assert model_class(shared_model_id) == shared_model_id

    def test_unknown_passthrough(self) -> None:
        from autoskillit.core import model_class

        assert model_class("custom-model-xyz") == "custom-model-xyz"
