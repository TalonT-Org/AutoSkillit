"""Tests for run_skill command building, timeouts, env, model, and per-invocation markers."""

from __future__ import annotations

from pathlib import Path

import pytest

from autoskillit.config import (
    AutomationConfig,
    RunSkillConfig,
)
from autoskillit.execution.commands import _inject_completion_directive
from autoskillit.execution.headless import _session_log_dir
from autoskillit.server.tools.tools_execution import run_skill
from tests.conftest import _make_result
from tests.server.conftest import _SUCCESS_JSON

pytestmark = [pytest.mark.layer("server"), pytest.mark.small]


class TestRunSkillPluginDir:
    """T2: run_skill passes --plugin-dir to the claude command."""

    @pytest.mark.anyio
    async def test_run_skill_passes_plugin_dir(self, tool_ctx):
        """run_skill includes --plugin-dir and the plugin_dir from tool_ctx in the command."""
        tool_ctx.runner.push(_make_result(returncode=1))  # clone guard snapshot
        tool_ctx.runner.push(
            _make_result(
                0,
                '{"type": "result", "subtype": "success", "is_error": false,'
                ' "result": "done", "session_id": "s1"}',
                "",
            )
        )
        await run_skill("/investigate some-error", "/tmp")

        cmd = tool_ctx.runner.call_args_list[-1][0]
        assert "--plugin-dir" in cmd
        plugin_dir_idx = cmd.index("--plugin-dir")
        from autoskillit.core.types._type_plugin_source import DirectInstall

        assert isinstance(tool_ctx.plugin_source, DirectInstall)
        assert cmd[plugin_dir_idx + 1] == str(tool_ctx.plugin_source.plugin_dir)
        assert "--output-format" in cmd
        assert cmd[cmd.index("--output-format") + 1] == "stream-json"
        actual_cwd = tool_ctx.runner.call_args_list[-1][1]
        assert actual_cwd == Path("/tmp"), f"Subprocess cwd mismatch: {actual_cwd} != /tmp"


class TestRunSkillTimeoutFromConfig:
    """run_skill uses configurable timeouts."""

    @pytest.mark.anyio
    async def test_run_skill_timeout_from_config(self, tool_ctx):
        """run_skill uses _config.run_skill.timeout instead of hardcoded value."""
        cfg = AutomationConfig()
        cfg.run_skill = RunSkillConfig(timeout=120)
        cfg.safety.require_dry_walkthrough = False
        tool_ctx.config = cfg

        tool_ctx.runner.push(_make_result(returncode=1))  # clone guard snapshot
        tool_ctx.runner.push(
            _make_result(
                0,
                '{"type": "result", "subtype": "success", "is_error": false,'
                ' "result": "done", "session_id": "s1"}',
                "",
            )
        )
        await run_skill("/investigate foo", "/tmp")

        assert tool_ctx.runner.call_args_list[-1][2] == 120.0


class TestRunSkillInjectsCompletionDirective:
    """run_skill injects completion directive into the skill command."""

    @pytest.mark.anyio
    async def test_run_skill_injects_completion_directive(self, tool_ctx):
        """Skill command passed to claude -p contains the completion marker instruction."""
        cfg = AutomationConfig()
        cfg.safety.require_dry_walkthrough = False
        tool_ctx.config = cfg

        tool_ctx.runner.push(_make_result(returncode=1))  # clone guard snapshot
        tool_ctx.runner.push(
            _make_result(
                0,
                '{"type": "result", "subtype": "success", "is_error": false,'
                ' "result": "done", "session_id": "s1"}',
                "",
            )
        )
        await run_skill("/investigate foo", "/tmp")

        cmd = tool_ctx.runner.call_args_list[-1][0]
        prompt_idx = cmd.index("--print") + 1 if "--print" in cmd else cmd.index("-p") + 1
        skill_arg = cmd[prompt_idx]
        assert "%%ORDER_UP::" in skill_arg
        assert "ORCHESTRATION DIRECTIVE" in skill_arg

    def test_inject_completion_directive_prohibits_standalone_marker(self):
        """
        The directive wording must explicitly instruct the model to emit the marker
        in the SAME message as its substantive output, not as a standalone message.
        This prevents the model from interpreting the directive as a post-task acknowledgment.
        """
        result = _inject_completion_directive("/audit-impl", "%%ORDER_UP%%")
        lowered = result.lower()
        assert (
            "same message" in lowered
            or "not as a separate" in lowered
            or ("standalone" in lowered and "not" in lowered)
        ), f"Directive must prohibit standalone marker emission. Got: {result!r}"


class TestRunSkillEnvPrefix:
    """run_skill always injects AUTOSKILLIT_HEADLESS=1 and optionally CLAUDE_CODE_EXIT_AFTER_STOP_DELAY via the env kwarg."""  # noqa: E501

    @pytest.mark.anyio
    async def test_default_delay_populates_env(self, tool_ctx):
        tool_ctx.runner.push(_make_result(returncode=1))  # clone guard snapshot
        tool_ctx.runner.push(_make_result(0, _SUCCESS_JSON, ""))
        await run_skill("/investigate something", "/tmp")
        cmd, _cwd, _timeout, kwargs = tool_ctx.runner.call_args_list[-1]
        assert cmd[0] == "claude"
        env = kwargs["env"]
        assert env["AUTOSKILLIT_HEADLESS"] == "1"
        assert env["CLAUDE_CODE_EXIT_AFTER_STOP_DELAY"] == "2000"

    @pytest.mark.anyio
    async def test_zero_delay_omits_delay_env_var(self, tool_ctx):
        cfg = AutomationConfig()
        cfg.run_skill = RunSkillConfig(exit_after_stop_delay_ms=0)
        cfg.safety.require_dry_walkthrough = False
        tool_ctx.config = cfg
        tool_ctx.runner.push(_make_result(returncode=1))  # clone guard snapshot
        tool_ctx.runner.push(_make_result(0, _SUCCESS_JSON, ""))
        await run_skill("/investigate something", "/tmp")
        cmd, _cwd, _timeout, kwargs = tool_ctx.runner.call_args_list[-1]
        assert cmd[0] == "claude"
        env = kwargs["env"]
        assert env["AUTOSKILLIT_HEADLESS"] == "1"
        assert "CLAUDE_CODE_EXIT_AFTER_STOP_DELAY" not in env

    @pytest.mark.anyio
    async def test_custom_delay_value_in_env(self, tool_ctx):
        cfg = AutomationConfig()
        cfg.run_skill = RunSkillConfig(
            exit_after_stop_delay_ms=60000, natural_exit_grace_seconds=61.0
        )
        cfg.safety.require_dry_walkthrough = False
        tool_ctx.config = cfg
        tool_ctx.runner.push(_make_result(returncode=1))  # clone guard snapshot
        tool_ctx.runner.push(_make_result(0, _SUCCESS_JSON, ""))
        await run_skill("/investigate something", "/tmp")
        cmd, _cwd, _timeout, kwargs = tool_ctx.runner.call_args_list[-1]
        assert cmd[0] == "claude"
        env = kwargs["env"]
        assert env["AUTOSKILLIT_HEADLESS"] == "1"
        assert env["CLAUDE_CODE_EXIT_AFTER_STOP_DELAY"] == "60000"


class TestRunSkillPassesSessionLogDir:
    """run_skill passes session_log_dir derived from cwd."""

    @pytest.mark.anyio
    async def test_run_skill_passes_session_log_dir(self, tool_ctx):
        """runner receives session_log_dir derived from cwd."""
        cfg = AutomationConfig()
        cfg.safety.require_dry_walkthrough = False
        tool_ctx.config = cfg

        tool_ctx.runner.push(_make_result(returncode=1))  # clone guard snapshot
        tool_ctx.runner.push(
            _make_result(
                0,
                '{"type": "result", "subtype": "success", "is_error": false,'
                ' "result": "done", "session_id": "s1"}',
                "",
            )
        )
        await run_skill("/investigate foo", "/some/project")

        call_kwargs = tool_ctx.runner.call_args_list[-1][3]
        expected_dir = _session_log_dir("/some/project")
        assert call_kwargs["session_log_dir"] == expected_dir
        assert "-some-project" in str(expected_dir)


class TestRunSkillModel:
    """Tests for model parameter in run_skill."""

    _MOCK_STDOUT = (
        '{"type": "result", "subtype": "success", "is_error": false, '
        '"result": "done", "session_id": "s1"}'
    )

    # MOD_S1
    @pytest.mark.anyio
    async def test_run_skill_passes_model_flag(self, tool_ctx):
        tool_ctx.runner.push(_make_result(returncode=1))  # clone guard snapshot
        tool_ctx.runner.push(_make_result(0, self._MOCK_STDOUT, ""))
        await run_skill("/investigate error", "/tmp", model="sonnet")
        cmd = tool_ctx.runner.call_args_list[-1][0]
        assert "--model" in cmd
        assert cmd[cmd.index("--model") + 1] == "sonnet"

    # MOD_S3
    @pytest.mark.anyio
    async def test_run_skill_no_model_flag_when_empty(self, tool_ctx):
        tool_ctx.config.model.default = ""  # ← add this line
        tool_ctx.runner.push(_make_result(returncode=1))  # clone guard snapshot
        tool_ctx.runner.push(_make_result(0, self._MOCK_STDOUT, ""))
        await run_skill("/investigate error", "/tmp", model="")
        cmd = tool_ctx.runner.call_args_list[-1][0]
        assert "--model" not in cmd


class TestRunSkillPerInvocationMarker:
    """Per-invocation completion markers are unique across run_skill calls."""

    @pytest.mark.anyio
    async def test_run_skill_markers_are_unique_per_invocation(self, tool_ctx):
        """Two run_skill calls must generate different completion_marker values."""
        success_json = (
            '{"type": "result", "subtype": "success", "is_error": false,'
            ' "result": "done", "session_id": "s1"}'
        )
        tool_ctx.runner.push(_make_result(returncode=1))  # clone guard snapshot call 1
        tool_ctx.runner.push(_make_result(returncode=0, stdout=success_json))
        tool_ctx.runner.push(_make_result(returncode=1))  # clone guard snapshot call 2
        tool_ctx.runner.push(_make_result(returncode=0, stdout=success_json))

        await run_skill("/investigate a", cwd="/tmp")
        await run_skill("/investigate b", cwd="/tmp")

        calls = tool_ctx.runner.call_args_list
        claude_calls = [c for c in calls if c[0][0] == "claude"]
        assert len(claude_calls) >= 2
        marker1 = claude_calls[0][3]["completion_marker"]
        marker2 = claude_calls[1][3]["completion_marker"]
        assert marker1 != marker2
        assert "%%ORDER_UP::" in marker1
        assert "%%ORDER_UP::" in marker2
