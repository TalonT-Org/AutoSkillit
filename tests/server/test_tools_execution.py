"""Tests for autoskillit server execution tools."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import structlog.contextvars
import structlog.testing

from autoskillit.config import (
    AutomationConfig,
    ModelConfig,
    RunSkillConfig,
)
from autoskillit.core import SkillResult
from autoskillit.core.types import (
    CONTEXT_EXHAUSTION_MARKER,
    RETRY_RESPONSE_FIELDS,
    ChannelConfirmation,
    RetryReason,
    TerminationReason,
)
from autoskillit.execution.headless import (
    _build_skill_result,
    _ensure_skill_prefix,
    _inject_completion_directive,
    _resolve_model,
    _session_log_dir,
)
from autoskillit.execution.process import SubprocessResult
from autoskillit.server.helpers import (
    _check_dry_walkthrough,
    _run_subprocess,
)
from autoskillit.server.tools_execution import run_cmd, run_python, run_skill, run_skill_retry
from tests.conftest import _make_result, _make_timeout_result

_SUCCESS_JSON = (
    '{"type": "result", "subtype": "success", "is_error": false,'
    ' "result": "done", "session_id": "s1"}'
)


def _success_session_json(result_text: str) -> str:
    return json.dumps(
        {
            "type": "result",
            "subtype": "success",
            "result": result_text,
            "session_id": "test-session",
            "is_error": False,
        }
    )


def _failed_session_json() -> str:
    return json.dumps(
        {
            "type": "result",
            "subtype": "error",
            "result": "Task failed with an error",
            "session_id": "test-session",
            "is_error": True,
        }
    )


def _context_exhausted_session_json() -> str:
    """Session result that triggers context exhaustion / needs_retry detection."""
    return json.dumps(
        {
            "type": "result",
            "subtype": "error",
            "result": "prompt is too long",
            "session_id": "test-session",
            "is_error": True,
            "errors": ["prompt is too long"],
        }
    )


class TestRunCmd:
    """T1, T2: run_cmd executes commands and returns exit code semantics."""

    @pytest.mark.asyncio
    async def test_successful_command(self, tool_ctx):
        tool_ctx.runner.push(_make_result(0, "hello\n", ""))
        result = json.loads(await run_cmd(cmd="echo hello", cwd="/tmp"))

        assert result["success"] is True
        assert result["exit_code"] == 0
        assert "hello" in result["stdout"]
        assert len(tool_ctx.runner.call_args_list) == 1
        assert tool_ctx.runner.call_args_list[0][0] == ["bash", "-c", "echo hello"]

    @pytest.mark.asyncio
    async def test_failing_command(self, tool_ctx):
        tool_ctx.runner.push(_make_result(1, "", "error"))
        result = json.loads(await run_cmd(cmd="false", cwd="/tmp"))

        assert result["success"] is False
        assert result["exit_code"] == 1

    @pytest.mark.asyncio
    async def test_custom_timeout(self, tool_ctx):
        tool_ctx.runner.push(_make_result(0, "", ""))
        await run_cmd(cmd="sleep 1", cwd="/tmp", timeout=30)

        assert tool_ctx.runner.call_args_list[-1][2] == 30.0


class TestRunSkillPluginDir:
    """T2: run_skill and run_skill_retry pass --plugin-dir to the claude command."""

    @pytest.mark.asyncio
    async def test_run_skill_passes_plugin_dir(self, tool_ctx):
        """run_skill includes --plugin-dir and the plugin_dir from tool_ctx in the command."""
        tool_ctx.runner.push(
            _make_result(
                0,
                '{"type": "result", "subtype": "success", "is_error": false,'
                ' "result": "done", "session_id": "s1"}',
                "",
            )
        )
        await run_skill("/investigate some-error", "/tmp")

        cmd = tool_ctx.runner.call_args_list[0][0]
        assert "--plugin-dir" in cmd
        plugin_dir_idx = cmd.index("--plugin-dir")
        assert cmd[plugin_dir_idx + 1] == tool_ctx.plugin_dir

    @pytest.mark.asyncio
    async def test_run_skill_retry_passes_plugin_dir(self, tool_ctx):
        """run_skill_retry includes --plugin-dir from tool_ctx in the command."""
        tool_ctx.runner.push(
            _make_result(
                0,
                '{"type": "result", "subtype": "success", "is_error": false,'
                ' "result": "done", "session_id": "s1"}',
                "",
            )
        )
        await run_skill_retry("/investigate some-error", "/tmp")

        cmd = tool_ctx.runner.call_args_list[0][0]
        assert "--plugin-dir" in cmd
        plugin_dir_idx = cmd.index("--plugin-dir")
        assert cmd[plugin_dir_idx + 1] == tool_ctx.plugin_dir


class TestCheckDryWalkthrough:
    """Dry-walkthrough gate blocks both /autoskillit:implement-worktree variants."""

    def test_dry_walkthrough_gate_blocks_implement_no_merge(self, tool_ctx, tmp_path):
        """Gate blocks /autoskillit:implement-worktree-no-merge when plan lacks marker."""
        plan = tmp_path / "plan.md"
        plan.write_text("# My Plan\n\nSome content")
        result = _check_dry_walkthrough(
            f"/autoskillit:implement-worktree-no-merge {plan}", str(tmp_path)
        )
        assert result is not None
        parsed = json.loads(result)
        assert parsed["success"] is False
        assert parsed["is_error"] is True
        assert "dry-walked" in parsed["result"].lower()

    def test_dry_walkthrough_gate_passes_implement_no_merge(self, tool_ctx, tmp_path):
        """Gate allows /autoskillit:implement-worktree-no-merge when plan has marker."""
        plan = tmp_path / "plan.md"
        plan.write_text("Dry-walkthrough verified = TRUE\n# My Plan")
        result = _check_dry_walkthrough(
            f"/autoskillit:implement-worktree-no-merge {plan}", str(tmp_path)
        )
        assert result is None

    def test_dry_walkthrough_gate_still_works_for_implement_worktree(self, tool_ctx, tmp_path):
        """Original /autoskillit:implement-worktree gating is not broken."""
        plan = tmp_path / "plan.md"
        plan.write_text("# No marker plan")
        result = _check_dry_walkthrough(f"/autoskillit:implement-worktree {plan}", str(tmp_path))
        assert result is not None
        parsed = json.loads(result)
        assert parsed["success"] is False
        assert parsed["is_error"] is True

    def test_dry_walkthrough_gate_ignores_unrelated_skills(self, tool_ctx):
        """Gate ignores skills that are not implement-worktree variants."""
        result = _check_dry_walkthrough("/autoskillit:investigate some-error", "/tmp")
        assert result is None

    def test_dry_walkthrough_gate_with_part_a_named_file_marked(self, tmp_path, tool_ctx):
        """Gate accepts _part_a.md file when marker is present."""
        plan = tmp_path / "task_plan_2026-01-01_part_a.md"
        plan.write_text("Dry-walkthrough verified = TRUE\n\nContent here")
        result = _check_dry_walkthrough(
            f"/autoskillit:implement-worktree-no-merge {plan}", str(tmp_path)
        )
        assert result is None

    def test_dry_walkthrough_gate_with_part_b_named_file_unmarked(self, tmp_path, tool_ctx):
        """Gate blocks _part_b.md file when marker is absent."""
        plan = tmp_path / "task_plan_2026-01-01_part_b.md"
        plan.write_text("> **PART B ONLY.**\n\nNo walkthrough marker here")
        result = _check_dry_walkthrough(
            f"/autoskillit:implement-worktree-no-merge {plan}", str(tmp_path)
        )
        assert result is not None
        parsed = json.loads(result)
        assert parsed["subtype"] == "gate_error"

    def test_dry_walkthrough_gate_distinguishes_parts_independently(self, tmp_path, tool_ctx):
        """Gate correctly distinguishes marked part_a from unmarked part_b."""
        part_a = tmp_path / "task_plan_part_a.md"
        part_b = tmp_path / "task_plan_part_b.md"
        part_a.write_text("Dry-walkthrough verified = TRUE\n\nPart A content")
        part_b.write_text("> **PART B ONLY.**\n\nPart B content — no marker")

        result_a = _check_dry_walkthrough(
            f"/autoskillit:implement-worktree-no-merge {part_a}", str(tmp_path)
        )
        result_b = _check_dry_walkthrough(
            f"/autoskillit:implement-worktree-no-merge {part_b}", str(tmp_path)
        )
        assert result_a is None
        assert result_b is not None
        assert json.loads(result_b)["subtype"] == "gate_error"


class TestRunSkillRetryGate:
    """run_skill_retry applies dry-walkthrough gate to implement skills."""

    @pytest.mark.asyncio
    async def test_run_skill_retry_gates_implement_no_merge(self, tool_ctx, tmp_path):
        """run_skill_retry gates /autoskillit:implement-worktree-no-merge."""
        plan = tmp_path / "plan.md"
        plan.write_text("# No marker plan")
        result = json.loads(
            await run_skill_retry(
                f"/autoskillit:implement-worktree-no-merge {plan}", str(tmp_path)
            )
        )
        assert result["success"] is False
        assert result["is_error"] is True
        assert "dry-walked" in result["result"].lower()


class TestRunSubprocessDelegatesToManaged:
    """Verify _run_subprocess delegates to the runner (ToolContext.runner) correctly."""

    @pytest.mark.asyncio
    async def test_normal_completion(self, tool_ctx):
        tool_ctx.runner.push(_make_result(0, "output", ""))
        rc, stdout, stderr = await _run_subprocess(["echo", "hi"], cwd="/tmp", timeout=10)
        assert rc == 0
        assert stdout == "output"
        assert stderr == ""

    @pytest.mark.asyncio
    async def test_timeout_returns_minus_one(self, tool_ctx):
        tool_ctx.runner.push(_make_timeout_result())
        rc, stdout, stderr = await _run_subprocess(["sleep", "999"], cwd="/tmp", timeout=1)
        assert rc == -1
        assert "timed out" in stderr


class TestProcessRunnerResult:
    """_process_runner_result shared helper lives in server.helpers."""

    def test_normal_exit_preserves_fields(self):
        from autoskillit.core import TerminationReason
        from autoskillit.execution.process import SubprocessResult
        from autoskillit.server.helpers import _process_runner_result

        result = SubprocessResult(
            returncode=0,
            stdout="hello",
            stderr="",
            termination=TerminationReason.NATURAL_EXIT,
            pid=1,
        )
        rc, stdout, stderr = _process_runner_result(result, timeout=10)
        assert rc == 0
        assert stdout == "hello"
        assert stderr == ""

    def test_timed_out_returns_minus_one_with_message(self):
        from autoskillit.core import TerminationReason
        from autoskillit.execution.process import SubprocessResult
        from autoskillit.server.helpers import _process_runner_result

        result = SubprocessResult(
            returncode=-1,
            stdout="partial",
            stderr="",
            termination=TerminationReason.TIMED_OUT,
            pid=1,
        )
        rc, stdout, stderr = _process_runner_result(result, timeout=5)
        assert rc == -1
        assert stdout == "partial"
        assert "timed out" in stderr
        assert "5" in stderr


class TestRunPython:
    """run_python tool: import, call, timeout, async support."""

    @pytest.fixture(autouse=True)
    def _setup_ctx(self, tool_ctx):
        """Initialize ToolContext for all run_python tests."""

    @pytest.mark.asyncio
    async def test_calls_function(self):
        """run_python imports module, calls function, returns JSON result."""
        result = json.loads(
            await run_python(
                callable="json.dumps",
                args={"obj": {"key": "value"}},
                timeout=10,
            )
        )
        assert result["success"] is True
        assert result["result"] == '{"key": "value"}'

    @pytest.mark.asyncio
    async def test_import_error(self):
        """run_python returns error for non-existent module."""
        result = json.loads(
            await run_python(
                callable="nonexistent_module.some_func",
                timeout=10,
            )
        )
        assert result["success"] is False
        assert "import" in result["error"].lower()

    @pytest.mark.asyncio
    async def test_not_callable(self):
        """run_python returns error when target is not callable."""
        result = json.loads(
            await run_python(
                callable="json.decoder",
                timeout=10,
            )
        )
        assert result["success"] is False
        assert "callable" in result["error"].lower()

    @pytest.mark.asyncio
    async def test_timeout(self):
        """run_python returns error on timeout."""
        import asyncio as _aio
        from unittest.mock import patch

        async def _hang(**_kw: object) -> None:
            await _aio.sleep(300)

        mock_module = MagicMock()
        mock_module.hang_fn = _hang

        with patch("importlib.import_module", return_value=mock_module):
            result = json.loads(
                await run_python(
                    callable="fake_mod.hang_fn",
                    timeout=1,
                )
            )
        assert result["success"] is False
        assert "timeout" in result["error"].lower()

    @pytest.mark.asyncio
    async def test_async_function(self):
        """run_python correctly awaits async functions."""
        result = json.loads(
            await run_python(
                callable="asyncio.sleep",
                args={"delay": 0},
                timeout=5,
            )
        )
        assert result["success"] is True

    @pytest.mark.asyncio
    async def test_sync_timeout_logs_warning(self):
        """run_python emits a warning log when TimeoutError is raised."""
        import asyncio as _aio

        async def _hang(**_kw: object) -> None:
            await _aio.sleep(300)

        mock_module = MagicMock()
        mock_module.hang_fn = _hang

        with (
            patch("importlib.import_module", return_value=mock_module),
            structlog.testing.capture_logs() as logs,
        ):
            result = json.loads(await run_python(callable="fake_mod.hang_fn", timeout=1))
        assert result["success"] is False
        assert "timeout" in result["error"].lower()
        assert any(log.get("log_level") == "warning" for log in logs), (
            f"Expected a warning log entry for timeout, got: {logs}"
        )
        assert any("timed out" in log.get("event", "").lower() for log in logs), (
            f"Expected 'timed out' in warning event, got: {logs}"
        )


class TestEnsureSkillPrefix:
    """Unit tests for _ensure_skill_prefix helper."""

    def test_adds_use_to_slash_command(self):
        assert _ensure_skill_prefix("/investigate error") == "Use /investigate error"

    def test_adds_use_to_namespaced_skill(self):
        assert (
            _ensure_skill_prefix("/autoskillit:investigate error")
            == "Use /autoskillit:investigate error"
        )

    def test_no_double_prefix(self):
        assert _ensure_skill_prefix("Use /investigate error") == "Use /investigate error"

    def test_ignores_plain_prompts(self):
        assert _ensure_skill_prefix("Fix the bug in main.py") == "Fix the bug in main.py"

    def test_handles_leading_whitespace(self):
        assert _ensure_skill_prefix("  /investigate error") == "Use /investigate error"


class TestRunSkillPrefix:
    """run_skill passes prefixed command to subprocess."""

    @pytest.mark.asyncio
    async def test_run_skill_prefixes_skill_command(self, tool_ctx):
        tool_ctx.runner.push(
            _make_result(
                0,
                '{"type": "result", "subtype": "success", "is_error": false, '
                '"result": "done", "session_id": "s1"}',
                "",
            )
        )
        await run_skill("/investigate error", "/tmp")
        cmd = tool_ctx.runner.call_args_list[0][0]
        assert cmd[4].startswith("Use /investigate error")

    @pytest.mark.asyncio
    async def test_run_skill_no_prefix_for_plain_prompt(self, tool_ctx):
        tool_ctx.runner.push(
            _make_result(
                0,
                '{"type": "result", "subtype": "success", "is_error": false, '
                '"result": "done", "session_id": "s1"}',
                "",
            )
        )
        await run_skill("Fix the bug in main.py", "/tmp")
        cmd = tool_ctx.runner.call_args_list[0][0]
        assert cmd[4].startswith("Fix the bug in main.py")

    @pytest.mark.asyncio
    async def test_run_skill_includes_completion_directive(self, tool_ctx):
        tool_ctx.runner.push(
            _make_result(
                0,
                '{"type": "result", "subtype": "success", "is_error": false, '
                '"result": "done", "session_id": "s1"}',
                "",
            )
        )
        await run_skill("/investigate error", "/tmp")
        cmd = tool_ctx.runner.call_args_list[0][0]
        assert "%%ORDER_UP%%" in cmd[4]


class TestRunSkillRetryPrefix:
    """run_skill_retry passes prefixed command to subprocess."""

    @pytest.mark.asyncio
    async def test_run_skill_retry_prefixes_skill_command(self, tool_ctx):
        tool_ctx.runner.push(
            _make_result(
                0,
                '{"type": "result", "subtype": "success", "is_error": false, '
                '"result": "done", "session_id": "s1"}',
                "",
            )
        )
        await run_skill_retry("/investigate error", "/tmp")
        cmd = tool_ctx.runner.call_args_list[0][0]
        assert cmd[4].startswith("Use /investigate error")

    @pytest.mark.asyncio
    async def test_run_skill_retry_no_prefix_for_plain_prompt(self, tool_ctx):
        tool_ctx.runner.push(
            _make_result(
                0,
                '{"type": "result", "subtype": "success", "is_error": false, '
                '"result": "done", "session_id": "s1"}',
                "",
            )
        )
        await run_skill_retry("Fix the bug in main.py", "/tmp")
        cmd = tool_ctx.runner.call_args_list[0][0]
        assert cmd[4].startswith("Fix the bug in main.py")


class TestDryWalkthroughGateWithPrefix:
    """Dry-walkthrough gate still receives raw command before prefix is applied."""

    @pytest.mark.asyncio
    async def test_gate_still_fires_for_implement_skill(self, tool_ctx, tmp_path):
        plan = tmp_path / "plan.md"
        plan.write_text("# No marker plan")
        result = json.loads(
            await run_skill(f"/autoskillit:implement-worktree {plan}", str(tmp_path))
        )
        assert result["success"] is False
        assert result["is_error"] is True
        assert "dry-walked" in result["result"].lower()


class TestRunSkillTimeoutFromConfig:
    """run_skill and run_skill_retry use configurable timeouts."""

    @pytest.mark.asyncio
    async def test_run_skill_timeout_from_config(self, tool_ctx):
        """run_skill uses _config.run_skill.timeout instead of hardcoded value."""
        cfg = AutomationConfig()
        cfg.run_skill = RunSkillConfig(timeout=120)
        cfg.safety.require_dry_walkthrough = False
        tool_ctx.config = cfg

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

    @pytest.mark.asyncio
    async def test_run_skill_injects_completion_directive(self, tool_ctx):
        """Skill command passed to claude -p contains the completion marker instruction."""
        cfg = AutomationConfig()
        cfg.safety.require_dry_walkthrough = False
        tool_ctx.config = cfg

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
        # The prompt argument is at index 4 (shifted by 2 env tokens)
        skill_arg = cmd[4]
        assert "%%ORDER_UP%%" in skill_arg
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
    """run_skill and run_skill_retry inject CLAUDE_CODE_EXIT_AFTER_STOP_DELAY env prefix."""

    @pytest.mark.asyncio
    async def test_default_delay_prepends_env_to_cmd(self, tool_ctx):
        tool_ctx.runner.push(_make_result(0, _SUCCESS_JSON, ""))
        await run_skill("/investigate something", "/tmp")
        cmd = tool_ctx.runner.call_args_list[0][0]
        assert cmd[0] == "env"
        assert cmd[1] == "CLAUDE_CODE_EXIT_AFTER_STOP_DELAY=120000"
        assert "claude" in cmd

    @pytest.mark.asyncio
    async def test_zero_delay_omits_env_prefix(self, tool_ctx):
        cfg = AutomationConfig()
        cfg.run_skill = RunSkillConfig(exit_after_stop_delay_ms=0)
        cfg.safety.require_dry_walkthrough = False
        tool_ctx.config = cfg
        tool_ctx.runner.push(_make_result(0, _SUCCESS_JSON, ""))
        await run_skill("/investigate something", "/tmp")
        cmd = tool_ctx.runner.call_args_list[0][0]
        assert cmd[0] != "env"
        assert cmd[0] == "claude"

    @pytest.mark.asyncio
    async def test_custom_delay_value_in_cmd(self, tool_ctx):
        cfg = AutomationConfig()
        cfg.run_skill = RunSkillConfig(exit_after_stop_delay_ms=60000)
        cfg.safety.require_dry_walkthrough = False
        tool_ctx.config = cfg
        tool_ctx.runner.push(_make_result(0, _SUCCESS_JSON, ""))
        await run_skill("/investigate something", "/tmp")
        cmd = tool_ctx.runner.call_args_list[0][0]
        assert cmd[0] == "env"
        assert cmd[1] == "CLAUDE_CODE_EXIT_AFTER_STOP_DELAY=60000"

    @pytest.mark.asyncio
    async def test_run_skill_retry_also_gets_env_prefix(self, tool_ctx):
        tool_ctx.runner.push(_make_result(0, _SUCCESS_JSON, ""))
        await run_skill_retry("/investigate something", "/tmp")
        cmd = tool_ctx.runner.call_args_list[0][0]
        assert cmd[0] == "env"
        assert cmd[1] == "CLAUDE_CODE_EXIT_AFTER_STOP_DELAY=120000"


class TestSessionLogDir:
    """Unit tests for _session_log_dir path derivation."""

    def test_replaces_slashes(self):
        result = _session_log_dir("/home/user/project")
        assert result == Path.home() / ".claude" / "projects" / "-home-user-project"

    def test_replaces_underscores(self):
        """Underscores must be replaced with dashes to match Claude Code's encoding."""
        result = _session_log_dir("/home/user/my_project")
        assert result == Path.home() / ".claude" / "projects" / "-home-user-my-project"

    def test_replaces_both_slashes_and_underscores(self):
        result = _session_log_dir("/home/user_name/my_project/sub_dir")
        assert (
            result == Path.home() / ".claude" / "projects" / "-home-user-name-my-project-sub-dir"
        )


class TestRunSkillPassesSessionLogDir:
    """run_skill passes session_log_dir derived from cwd."""

    @pytest.mark.asyncio
    async def test_run_skill_passes_session_log_dir(self, tool_ctx):
        """runner receives session_log_dir derived from cwd."""
        cfg = AutomationConfig()
        cfg.safety.require_dry_walkthrough = False
        tool_ctx.config = cfg

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


class TestRunSkillRetryPassesSessionLogDir:
    """run_skill_retry passes session_log_dir derived from cwd."""

    @pytest.mark.asyncio
    async def test_run_skill_retry_passes_session_log_dir(self, tool_ctx):
        """run_skill_retry must pass session_log_dir just like run_skill."""
        cfg = AutomationConfig()
        cfg.safety.require_dry_walkthrough = False
        tool_ctx.config = cfg

        tool_ctx.runner.push(
            _make_result(
                0,
                '{"type": "result", "subtype": "success", "is_error": false,'
                ' "result": "done", "session_id": "s1"}',
                "",
            )
        )
        await run_skill_retry("/investigate foo", "/some/project")

        call_kwargs = tool_ctx.runner.call_args_list[-1][3]
        expected_dir = _session_log_dir("/some/project")
        assert call_kwargs["session_log_dir"] == expected_dir


class TestStalenessReturnsNeedsRetry:
    """Stale SubprocessResult triggers needs_retry response."""

    def test_staleness_returns_needs_retry(self):
        """A stale result produces needs_retry=True, retry_reason='resume'."""
        stale_result = SubprocessResult(
            returncode=-1,
            stdout="",
            stderr="",
            termination=TerminationReason.STALE,
            pid=12345,
        )
        response = json.loads(_build_skill_result(stale_result).to_json())
        assert response["needs_retry"] is True
        assert response["retry_reason"] == "resume"
        assert response["subtype"] == "stale"
        assert response["success"] is False


class TestBuildSkillResultCrossValidation:
    """_build_skill_result cross-validates signals to produce unambiguous success."""

    EXPECTED_SKILL_KEYS = {
        "success",
        "result",
        "session_id",
        "subtype",
        "is_error",
        "exit_code",
        "needs_retry",
        "retry_reason",
        "stderr",
        "token_usage",
    }

    def test_empty_stdout_exit_zero_is_failure(self):
        """Exit 0 with empty stdout is NOT success — output was lost."""
        result_obj = SubprocessResult(
            returncode=0, stdout="", stderr="", termination=TerminationReason.NATURAL_EXIT, pid=1
        )
        response = json.loads(_build_skill_result(result_obj).to_json())
        assert response["success"] is False
        assert response["is_error"] is True

    def test_timed_out_session_is_failure(self):
        """Timed-out sessions are always failures, regardless of partial stdout."""
        result_obj = SubprocessResult(
            returncode=-1, stdout="", stderr="", termination=TerminationReason.TIMED_OUT, pid=1
        )
        response = json.loads(_build_skill_result(result_obj).to_json())
        assert response["success"] is False
        assert response["is_error"] is True
        assert response["subtype"] == "timeout"

    def test_stale_session_is_failure(self):
        """Stale sessions are failures (even though retriable)."""
        result_obj = SubprocessResult(
            returncode=-1, stdout="", stderr="", termination=TerminationReason.STALE, pid=1
        )
        response = json.loads(_build_skill_result(result_obj).to_json())
        assert response["success"] is False
        assert response["needs_retry"] is True

    def test_normal_success_has_success_true(self):
        """A valid session result with non-empty output is success."""
        valid_json = json.dumps(
            {
                "type": "result",
                "subtype": "success",
                "is_error": False,
                "result": "Task completed.",
                "session_id": "s1",
            }
        )
        result_obj = SubprocessResult(
            returncode=0,
            stdout=valid_json,
            stderr="",
            termination=TerminationReason.NATURAL_EXIT,
            pid=1,
        )
        response = json.loads(_build_skill_result(result_obj).to_json())
        assert response["success"] is True
        assert response["is_error"] is False
        assert response["result"] == "Task completed."

    def test_nonzero_exit_overrides_is_error_false(self):
        """Exit code != 0 means failure even if Claude wrote is_error=false."""
        valid_json = json.dumps(
            {
                "type": "result",
                "subtype": "success",
                "is_error": False,
                "result": "partial",
                "session_id": "s1",
            }
        )
        result_obj = SubprocessResult(
            returncode=1,
            stdout=valid_json,
            stderr="",
            termination=TerminationReason.NATURAL_EXIT,
            pid=1,
        )
        response = json.loads(_build_skill_result(result_obj).to_json())
        assert response["success"] is False

    def test_gate_disabled_schema(self, tool_ctx):
        """Gate-disabled response has standard keys."""
        from autoskillit.pipeline.gate import DefaultGateState
        from autoskillit.server.helpers import _require_enabled

        tool_ctx.gate = DefaultGateState(enabled=False)
        response = json.loads(_require_enabled())
        assert set(response.keys()) == self.EXPECTED_SKILL_KEYS

    def test_stale_schema(self):
        """Stale response has standard keys."""
        result_obj = SubprocessResult(
            returncode=-1, stdout="", stderr="", termination=TerminationReason.STALE, pid=1
        )
        response = json.loads(_build_skill_result(result_obj).to_json())
        assert set(response.keys()) == self.EXPECTED_SKILL_KEYS

    def test_timeout_schema(self):
        """Timeout response has standard keys."""
        result_obj = SubprocessResult(
            returncode=-1, stdout="", stderr="", termination=TerminationReason.TIMED_OUT, pid=1
        )
        response = json.loads(_build_skill_result(result_obj).to_json())
        assert set(response.keys()) == self.EXPECTED_SKILL_KEYS

    def test_normal_success_schema(self):
        """Normal success response has standard keys."""
        valid_json = json.dumps(
            {
                "type": "result",
                "subtype": "success",
                "is_error": False,
                "result": "Done.",
                "session_id": "s1",
            }
        )
        result_obj = SubprocessResult(
            returncode=0,
            stdout=valid_json,
            stderr="",
            termination=TerminationReason.NATURAL_EXIT,
            pid=1,
        )
        response = json.loads(_build_skill_result(result_obj).to_json())
        assert set(response.keys()) == self.EXPECTED_SKILL_KEYS

    def test_empty_stdout_schema(self):
        """Empty stdout response has standard keys."""
        result_obj = SubprocessResult(
            returncode=0, stdout="", stderr="", termination=TerminationReason.NATURAL_EXIT, pid=1
        )
        response = json.loads(_build_skill_result(result_obj).to_json())
        assert set(response.keys()) == self.EXPECTED_SKILL_KEYS


class TestGateErrorSchemaNormalization:
    """Gate errors use the standard 9-field response schema."""

    def test_require_enabled_gate_returns_standard_schema(self, tool_ctx):
        """Gate errors must use the same schema as normal responses."""
        from autoskillit.pipeline.gate import DefaultGateState
        from autoskillit.server.helpers import _require_enabled

        tool_ctx.gate = DefaultGateState(enabled=False)
        gate_result = _require_enabled()
        assert gate_result is not None
        response = json.loads(gate_result)
        assert response["success"] is False
        assert response["is_error"] is True
        assert response["needs_retry"] is False
        assert "result" in response

    def test_dry_walkthrough_gate_returns_standard_schema(self, tool_ctx, tmp_path):
        """Dry-walkthrough gate errors must use the standard response schema."""
        plan = tmp_path / "plan.md"
        plan.write_text("No marker here")
        skill_cmd = f"/autoskillit:implement-worktree {plan}"
        result = _check_dry_walkthrough(skill_cmd, str(tmp_path))
        assert result is not None
        response = json.loads(result)
        assert response["success"] is False
        assert response["is_error"] is True
        assert response["subtype"] == "gate_error"


class TestBuildSkillResultStderr:
    """_build_skill_result includes stderr in responses."""

    def test_stderr_included_in_response(self):
        """Subprocess stderr is surfaced in the response."""
        valid_json = json.dumps(
            {
                "type": "result",
                "subtype": "success",
                "is_error": False,
                "result": "Done.",
                "session_id": "s1",
            }
        )
        result_obj = SubprocessResult(
            returncode=0,
            stdout=valid_json,
            stderr="queue contention",
            termination=TerminationReason.NATURAL_EXIT,
            pid=1,
        )
        response = json.loads(_build_skill_result(result_obj).to_json())
        assert response["stderr"] == "queue contention"

    def test_stderr_truncated(self):
        """Stderr exceeding 5000 chars is truncated."""
        valid_json = json.dumps(
            {
                "type": "result",
                "subtype": "success",
                "is_error": False,
                "result": "Done.",
                "session_id": "s1",
            }
        )
        long_stderr = "x" * 6000
        result_obj = SubprocessResult(
            returncode=0,
            stdout=valid_json,
            stderr=long_stderr,
            termination=TerminationReason.NATURAL_EXIT,
            pid=1,
        )
        response = json.loads(_build_skill_result(result_obj).to_json())
        assert len(response["stderr"]) < len(long_stderr)
        assert "truncated" in response["stderr"]

    def test_empty_stderr_is_empty_string(self):
        """Empty stderr produces empty string, not omitted."""
        valid_json = json.dumps(
            {
                "type": "result",
                "subtype": "success",
                "is_error": False,
                "result": "Done.",
                "session_id": "s1",
            }
        )
        result_obj = SubprocessResult(
            returncode=0,
            stdout=valid_json,
            stderr="",
            termination=TerminationReason.NATURAL_EXIT,
            pid=1,
        )
        response = json.loads(_build_skill_result(result_obj).to_json())
        assert response["stderr"] == ""

    def test_stale_branch_has_empty_stderr(self):
        """Stale branch produces empty stderr (process killed before output)."""
        result_obj = SubprocessResult(
            returncode=-1, stdout="", stderr="", termination=TerminationReason.STALE, pid=1
        )
        response = json.loads(_build_skill_result(result_obj).to_json())
        assert response["stderr"] == ""


class TestRetryResponseFieldsIncludesStderr:
    """RETRY_RESPONSE_FIELDS schema includes stderr."""

    def test_stderr_in_fields(self):
        assert "stderr" in RETRY_RESPONSE_FIELDS

    def test_field_count(self):
        assert len(RETRY_RESPONSE_FIELDS) == 10


class TestContextExhaustionStructured:
    """_is_context_exhausted uses structured detection, not substring on result."""

    def test_context_exhaustion_not_triggered_by_model_prose(self):
        """Model output discussing prompt length must NOT trigger context exhaustion."""
        from autoskillit.execution.session import ClaudeSessionResult

        session = ClaudeSessionResult(
            subtype="error_during_execution",
            is_error=True,
            result="The user said: prompt is too long for this task",
            session_id="s1",
        )
        assert session.needs_retry is False
        assert session._is_context_exhausted() is False

    def test_real_context_exhaustion_still_detected(self):
        """Genuine context exhaustion (specific subtype) is still detected."""
        from autoskillit.execution.session import ClaudeSessionResult

        session = ClaudeSessionResult(
            subtype="error_during_execution",
            is_error=True,
            result="prompt is too long",
            session_id="s1",
            errors=["prompt is too long"],
        )
        assert session._is_context_exhausted() is True
        assert session.needs_retry is True


class TestParseFallbackRejectsUntypedJson:
    """parse_session_result fallback path requires type == result."""

    def test_parse_fallback_rejects_untyped_json(self):
        """Single JSON object without type=result must be rejected."""
        from autoskillit.execution.session import parse_session_result

        parsed = parse_session_result('{"error": "something broke"}')
        assert parsed.subtype == "unparseable"
        assert parsed.is_error is True


class TestCompletionViaMonitorKill:
    """Completion detected by monitor + kill returncode is not failure."""

    MARKER = "%%ORDER_UP%%"

    def test_completion_via_monitor_kill_is_not_failure(self):
        """When the session monitor detects completion and kills the process,
        returncode is -15 (SIGTERM). _compute_success should treat this as
        success when the session result envelope says success.
        """
        from autoskillit.execution.session import ClaudeSessionResult, _compute_success

        session = ClaudeSessionResult(
            subtype="success",
            is_error=False,
            result=f"Task completed successfully.\n\n{self.MARKER}",
            session_id="s1",
        )
        assert (
            _compute_success(
                session,
                returncode=-15,
                termination=TerminationReason.COMPLETED,
                completion_marker=self.MARKER,
            )
            is True
        )

    def test_completion_via_monitor_kill_returncode_zero(self):
        """PTY may mask signal codes to returncode=0 — COMPLETED still works."""
        from autoskillit.execution.session import ClaudeSessionResult, _compute_success

        session = ClaudeSessionResult(
            subtype="success",
            is_error=False,
            result=f"Task completed successfully.\n\n{self.MARKER}",
            session_id="s1",
        )
        assert (
            _compute_success(
                session,
                returncode=0,
                termination=TerminationReason.COMPLETED,
                completion_marker=self.MARKER,
            )
            is True
        )


class TestBuildSkillResultCompleted:
    """_build_skill_result and _compute_success handle COMPLETED termination correctly."""

    def test_build_skill_result_completed_nonempty_result_is_success(self):
        """COMPLETED + valid JSON stdout with non-empty result → success=True."""
        stdout = json.dumps(
            {
                "type": "result",
                "subtype": "success",
                "is_error": False,
                "result": "Task done.",
                "session_id": "s1",
            }
        )
        result = _make_result(
            returncode=-15,
            stdout=stdout,
            termination_reason=TerminationReason.COMPLETED,
        )
        parsed = json.loads(_build_skill_result(result).to_json())
        assert parsed["success"] is True

    def test_build_skill_result_completed_empty_result_is_failure(self):
        """COMPLETED + empty stdout + rc=-15 → success=False, needs_retry=True."""
        result = _make_result(
            returncode=-15,
            stdout="",
            termination_reason=TerminationReason.COMPLETED,
            channel_confirmation=ChannelConfirmation.UNMONITORED,
        )
        parsed = json.loads(_build_skill_result(result).to_json())
        assert parsed["success"] is False
        assert parsed["needs_retry"] is True

    def test_compute_success_completed_empty_result_returns_false(self):
        """Empty result with COMPLETED termination: bypass does NOT engage → returns False."""
        from autoskillit.execution.session import ClaudeSessionResult, _compute_success

        session = ClaudeSessionResult(
            subtype="empty_output",
            result="",
            is_error=True,
            session_id="s1",
        )
        assert (
            _compute_success(
                session,
                returncode=-15,
                termination=TerminationReason.COMPLETED,
            )
            is False
        )

    def test_success_empty_completed_returns_needs_retry_true(self, tool_ctx):
        """Full path: stdout with success+empty under COMPLETED → needs_retry=True."""
        stdout = json.dumps(
            {
                "type": "result",
                "subtype": "success",
                "is_error": False,
                "result": "",
                "session_id": "s1",
            }
        )
        result = _make_result(
            returncode=0,
            stdout=stdout,
            termination_reason=TerminationReason.COMPLETED,
            channel_confirmation=ChannelConfirmation.UNMONITORED,
        )
        parsed = json.loads(
            _build_skill_result(result, completion_marker="", skill_command="/test").to_json()
        )
        assert parsed["success"] is False
        assert parsed["needs_retry"] is True
        assert parsed["retry_reason"] == RetryReason.RESUME.value
        assert parsed["subtype"] == "success"

    def test_success_empty_completed_subtype_captured_in_audit_log(self, tool_ctx):
        """_capture_failure must be called with subtype='success' for audit log integrity."""
        stdout = json.dumps(
            {
                "type": "result",
                "subtype": "success",
                "is_error": False,
                "result": "",
                "session_id": "s1",
            }
        )
        result = _make_result(
            returncode=0,
            stdout=stdout,
            termination_reason=TerminationReason.COMPLETED,
            channel_confirmation=ChannelConfirmation.UNMONITORED,
        )
        _build_skill_result(
            result, completion_marker="", skill_command="/test", audit=tool_ctx.audit
        )
        report = tool_ctx.audit.get_report()
        assert len(report) == 1
        assert report[0].subtype == "success"
        assert report[0].needs_retry is True


class TestRunSkillRetryConsolidation:
    """run_skill_retry delegates to ctx.executor.run() with retry-specific config."""

    @pytest.fixture(autouse=True)
    def _setup_ctx(self, tool_ctx):
        """Initialize ToolContext for run_skill_retry consolidation tests."""
        self._tool_ctx = tool_ctx

    @pytest.mark.asyncio
    async def test_run_skill_retry_passes_add_dir_to_subprocess(self):
        """add_dir is forwarded to ctx.executor.run()."""
        mock_result = SkillResult(
            success=True,
            result="ok",
            session_id="s1",
            subtype="success",
            is_error=False,
            exit_code=0,
            needs_retry=False,
            retry_reason=RetryReason.NONE,
            stderr="",
        )
        mock_run = AsyncMock(return_value=mock_result)
        self._tool_ctx.executor = type("MockExec", (), {"run": mock_run})()
        await run_skill_retry("/investigate something", "/tmp", add_dir="/extra/dir")

        assert mock_run.call_args.kwargs.get("add_dir") == "/extra/dir"

    @pytest.mark.asyncio
    async def test_run_skill_retry_uses_retry_timeout_not_skill_timeout(self):
        """run_skill_retry passes RunSkillRetryConfig.timeout (7200) not RunSkillConfig (3600)."""
        mock_result = SkillResult(
            success=True,
            result="ok",
            session_id="s1",
            subtype="success",
            is_error=False,
            exit_code=0,
            needs_retry=False,
            retry_reason=RetryReason.NONE,
            stderr="",
        )
        mock_run = AsyncMock(return_value=mock_result)
        self._tool_ctx.executor = type("MockExec", (), {"run": mock_run})()
        await run_skill_retry("/investigate something", "/tmp")

        assert mock_run.call_args.kwargs.get("timeout") == 7200


class TestMarkerCrossValidation:
    """Completion marker cross-validation catches misclassified sessions."""

    MARKER = "%%ORDER_UP%%"

    def test_marker_only_result_is_not_success(self):
        """Result containing only the marker with no real content is failure."""
        from autoskillit.execution.session import ClaudeSessionResult, _compute_success

        session = ClaudeSessionResult(
            subtype="success",
            is_error=False,
            result=self.MARKER,
            session_id="s1",
        )
        assert (
            _compute_success(
                session,
                returncode=0,
                termination=TerminationReason.NATURAL_EXIT,
                completion_marker=self.MARKER,
            )
            is False
        )

    def test_marker_stripped_from_result(self):
        """_build_skill_result strips the completion marker from result text."""
        valid_json = json.dumps(
            {
                "type": "result",
                "subtype": "success",
                "is_error": False,
                "result": f"Task completed.\n\n{self.MARKER}",
                "session_id": "s1",
            }
        )
        result_obj = SubprocessResult(
            returncode=0,
            stdout=valid_json,
            stderr="",
            termination=TerminationReason.NATURAL_EXIT,
            pid=1,
        )
        response = json.loads(
            _build_skill_result(result_obj, completion_marker=self.MARKER).to_json()
        )
        assert self.MARKER not in response["result"]
        assert "Task completed." in response["result"]

    def test_natural_exit_without_marker_not_success(self):
        """Session claims success but never wrote the marker — not success."""
        from autoskillit.execution.session import ClaudeSessionResult, _compute_success

        session = ClaudeSessionResult(
            subtype="success",
            is_error=False,
            result="Some partial output",
            session_id="s1",
        )
        assert (
            _compute_success(
                session,
                returncode=0,
                termination=TerminationReason.NATURAL_EXIT,
                completion_marker=self.MARKER,
            )
            is False
        )

    def test_termination_reason_natural_exit(self):
        """NATURAL_EXIT with marker in result is success."""
        from autoskillit.execution.session import ClaudeSessionResult, _compute_success

        session = ClaudeSessionResult(
            subtype="success",
            is_error=False,
            result=f"Done.\n\n{self.MARKER}",
            session_id="s1",
        )
        assert (
            _compute_success(
                session,
                returncode=0,
                termination=TerminationReason.NATURAL_EXIT,
                completion_marker=self.MARKER,
            )
            is True
        )

    def test_termination_reason_completed(self):
        """COMPLETED termination with marker in result is success."""
        from autoskillit.execution.session import ClaudeSessionResult, _compute_success

        session = ClaudeSessionResult(
            subtype="success",
            is_error=False,
            result=f"Done.\n\n{self.MARKER}",
            session_id="s1",
        )
        assert (
            _compute_success(
                session,
                returncode=0,
                termination=TerminationReason.COMPLETED,
                completion_marker=self.MARKER,
            )
            is True
        )

    def test_termination_reason_completed_without_marker_fails(self):
        """COMPLETED but result doesn't contain marker — not success."""
        from autoskillit.execution.session import ClaudeSessionResult, _compute_success

        session = ClaudeSessionResult(
            subtype="success",
            is_error=False,
            result="Some output without marker",
            session_id="s1",
        )
        assert (
            _compute_success(
                session,
                returncode=0,
                termination=TerminationReason.COMPLETED,
                completion_marker=self.MARKER,
            )
            is False
        )

    MARKER = "%%ORDER_UP%%"

    @pytest.mark.parametrize(
        "termination,returncode,result_text,expected",
        [
            (TerminationReason.NATURAL_EXIT, 0, f"Done.\n\n{MARKER}", True),
            (TerminationReason.NATURAL_EXIT, 0, "No marker here", False),
            (TerminationReason.NATURAL_EXIT, 0, MARKER, False),  # marker-only
            (TerminationReason.COMPLETED, 0, f"Done.\n\n{MARKER}", True),
            (TerminationReason.COMPLETED, -15, f"Done.\n\n{MARKER}", True),
            (TerminationReason.COMPLETED, 0, "No marker here", False),
            (TerminationReason.STALE, -15, f"Done.\n\n{MARKER}", False),
            (TerminationReason.TIMED_OUT, -1, f"Done.\n\n{MARKER}", False),
        ],
        ids=[
            "natural_exit+marker=success",
            "natural_exit+no_marker=failure",
            "natural_exit+marker_only=failure",
            "completed+marker=success",
            "completed_sigterm+marker=success",
            "completed+no_marker=failure",
            "stale+marker=failure",
            "timed_out+marker=failure",
        ],
    )
    def test_cross_validation_matrix(self, termination, returncode, result_text, expected):
        """Full cross-validation matrix for termination x marker presence."""
        from autoskillit.execution.session import ClaudeSessionResult, _compute_success

        session = ClaudeSessionResult(
            subtype="success",
            is_error=False,
            result=result_text,
            session_id="s1",
        )
        assert (
            _compute_success(
                session,
                returncode=returncode,
                termination=termination,
                completion_marker=self.MARKER,
            )
            is expected
        )

    def test_build_skill_result_recovers_when_marker_in_separate_assistant_message(self):
        """
        If the model emits substantive content in an assistant record and %%ORDER_UP%%
        as a separate final message, _build_skill_result must return success=True with
        the substantive content — not success=False with empty result.
        """
        marker = self.MARKER
        ndjson = (
            '{"type":"assistant","message":{"role":"assistant",'
            '"content":"Detailed audit report.\\nGO verdict."}}\n'
            '{"type":"assistant","message":{"role":"assistant","content":"%%ORDER_UP%%"}}\n'
            '{"type":"result","subtype":"success","result":"%%ORDER_UP%%",'
            '"session_id":"s1","is_error":false}\n'
        )
        result = _build_skill_result(
            SubprocessResult(
                returncode=0,
                stdout=ndjson,
                stderr="",
                termination=TerminationReason.NATURAL_EXIT,
                pid=1,
            ),
            completion_marker=marker,
            skill_command="audit-impl",
            audit=None,
        )
        assert result.success is True
        assert "Detailed audit report." in result.result
        assert marker not in result.result
        assert result.needs_retry is False

    def test_build_skill_result_does_not_recover_when_only_marker_in_assistant(self):
        """
        If ALL assistant records contain only the marker and result is also marker-only,
        recovery must not produce a false positive — there is no substantive content.
        """
        marker = self.MARKER
        ndjson = (
            '{"type":"assistant","message":{"role":"assistant","content":"%%ORDER_UP%%"}}\n'
            '{"type":"result","subtype":"success","result":"%%ORDER_UP%%",'
            '"session_id":"s1","is_error":false}\n'
        )
        result = _build_skill_result(
            SubprocessResult(
                returncode=0,
                stdout=ndjson,
                stderr="",
                termination=TerminationReason.NATURAL_EXIT,
                pid=1,
            ),
            completion_marker=marker,
            skill_command="",
            audit=None,
        )
        assert result.success is False


class TestRunSkillRetrySessionOutcome:
    """run_skill_retry correctly classifies all Claude Code session outcomes."""

    @pytest.mark.asyncio
    async def test_detects_max_turns_via_subtype(self, tool_ctx):
        """error_max_turns in JSON output -> needs_retry=True, retry_reason=RESUME."""
        stdout = json.dumps(
            {
                "type": "result",
                "subtype": "error_max_turns",
                "is_error": False,
                "session_id": "s1",
                "errors": ["Max turns reached"],
            }
        )
        tool_ctx.runner.push(_make_result(1, stdout, ""))
        result = json.loads(await run_skill_retry("/retry-worktree plan.md", "/tmp"))
        assert result["needs_retry"] is True
        assert result["retry_reason"] == RetryReason.RESUME

    @pytest.mark.asyncio
    async def test_detects_context_limit(self, tool_ctx):
        """'Prompt is too long' -> needs_retry=True, retry_reason='retry'."""
        stdout = json.dumps(
            {
                "type": "result",
                "subtype": "success",
                "is_error": True,
                "result": "Prompt is too long",
                "session_id": "s1",
            }
        )
        tool_ctx.runner.push(_make_result(1, stdout, ""))
        result = json.loads(await run_skill_retry("/retry-worktree plan.md", "/tmp"))
        assert result["needs_retry"] is True
        assert result["retry_reason"] == RetryReason.RESUME

    @pytest.mark.asyncio
    async def test_success_not_retriable(self, tool_ctx):
        """Normal success -> needs_retry=False."""
        stdout = json.dumps(
            {
                "type": "result",
                "subtype": "success",
                "is_error": False,
                "result": "Done.",
                "session_id": "s1",
            }
        )
        tool_ctx.runner.push(_make_result(0, stdout, ""))
        result = json.loads(await run_skill_retry("/retry-worktree plan.md", "/tmp"))
        assert result["needs_retry"] is False
        assert result["retry_reason"] == RetryReason.NONE

    @pytest.mark.asyncio
    async def test_execution_error_not_retriable(self, tool_ctx):
        """error_during_execution -> needs_retry=False."""
        stdout = json.dumps(
            {
                "type": "result",
                "subtype": "error_during_execution",
                "is_error": True,
                "session_id": "s1",
                "errors": ["crashed"],
            }
        )
        tool_ctx.runner.push(_make_result(1, stdout, ""))
        result = json.loads(await run_skill_retry("/retry-worktree plan.md", "/tmp"))
        assert result["needs_retry"] is False

    @pytest.mark.asyncio
    async def test_unparseable_stdout_not_retriable(self, tool_ctx):
        """Non-JSON stdout -> needs_retry=False."""
        tool_ctx.runner.push(_make_result(1, "crash dump", "segfault"))
        result = json.loads(await run_skill_retry("/retry-worktree plan.md", "/tmp"))
        assert result["needs_retry"] is False


class TestRunSkillRetryAgentResult:
    """run_skill_retry result field contains actionable text."""

    @pytest.mark.asyncio
    async def test_context_limit_result_is_actionable(self, tool_ctx):
        """When context is exhausted, result text must NOT say 'Prompt is too long'."""
        stdout = json.dumps(
            {
                "type": "result",
                "subtype": "success",
                "is_error": True,
                "result": "Prompt is too long",
                "session_id": "s1",
            }
        )
        tool_ctx.runner.push(_make_result(1, stdout, ""))
        result = json.loads(await run_skill_retry("/retry-worktree plan.md", "/tmp"))
        assert "prompt is too long" not in result["result"].lower()
        assert result["needs_retry"] is True

    @pytest.mark.asyncio
    async def test_normal_success_result_passes_through(self, tool_ctx):
        """Normal success result text is preserved."""
        stdout = json.dumps(
            {
                "type": "result",
                "subtype": "success",
                "is_error": False,
                "result": "Done.",
                "session_id": "s1",
            }
        )
        tool_ctx.runner.push(_make_result(0, stdout, ""))
        result = json.loads(await run_skill_retry("/retry-worktree plan.md", "/tmp"))
        assert result["result"] == "Done."


class TestRunSkillRetryFields:
    """run_skill includes needs_retry and retry_reason for parity."""

    @pytest.mark.asyncio
    async def test_includes_needs_retry_false(self, tool_ctx):
        """run_skill response includes needs_retry=False on normal success."""
        stdout = json.dumps(
            {
                "type": "result",
                "subtype": "success",
                "is_error": False,
                "result": "Done.",
                "session_id": "s1",
            }
        )
        tool_ctx.runner.push(_make_result(0, stdout, ""))
        result = json.loads(await run_skill("/investigate error", "/tmp"))
        assert result["needs_retry"] is False
        assert result["retry_reason"] == RetryReason.NONE

    @pytest.mark.asyncio
    async def test_includes_needs_retry_true_on_context_limit(self, tool_ctx):
        """run_skill response includes needs_retry=True when context is exhausted."""
        stdout = json.dumps(
            {
                "type": "result",
                "subtype": "success",
                "is_error": True,
                "result": "Prompt is too long",
                "session_id": "s1",
            }
        )
        tool_ctx.runner.push(_make_result(1, stdout, ""))
        result = json.loads(await run_skill("/investigate error", "/tmp"))
        assert result["needs_retry"] is True
        assert result["retry_reason"] == RetryReason.RESUME
        assert "prompt is too long" not in result["result"].lower()


class TestRunSkillFailurePaths:
    """run_skill surfaces session outcome on failure."""

    @pytest.mark.asyncio
    async def test_returns_subtype_on_incomplete_session(self, tool_ctx):
        """run_skill includes subtype when session didn't finish."""
        stdout = json.dumps(
            {
                "type": "result",
                "subtype": "error_max_turns",
                "is_error": False,
                "session_id": "s1",
            }
        )
        tool_ctx.runner.push(_make_result(1, stdout, ""))
        result = json.loads(await run_skill("/investigate error", "/tmp"))
        assert result["session_id"] == "s1"
        assert result["subtype"] == "error_max_turns"

    @pytest.mark.asyncio
    async def test_returns_is_error_on_context_limit(self, tool_ctx):
        """run_skill includes is_error when context limit is hit."""
        stdout = json.dumps(
            {
                "type": "result",
                "subtype": "success",
                "is_error": True,
                "result": "Prompt is too long",
                "session_id": "s1",
            }
        )
        tool_ctx.runner.push(_make_result(1, stdout, ""))
        result = json.loads(await run_skill("/investigate error", "/tmp"))
        assert result["is_error"] is True
        assert result["subtype"] == "success"

    @pytest.mark.asyncio
    async def test_handles_empty_stdout(self, tool_ctx):
        """run_skill returns error result when stdout is empty."""
        tool_ctx.runner.push(_make_result(1, "", "segfault", channel_confirmation=ChannelConfirmation.UNMONITORED))
        result = json.loads(await run_skill("/investigate error", "/tmp"))
        assert result["exit_code"] == 1
        assert result["is_error"] is True
        assert result["subtype"] == "empty_output"
        assert result["success"] is False

    @pytest.mark.asyncio
    async def test_empty_stdout_exit_zero_is_retriable(self, tool_ctx):
        """Infrastructure failure (empty stdout, exit 0) is retriable with stderr."""
        tool_ctx.runner.push(_make_result(0, "", "session dropped", channel_confirmation=ChannelConfirmation.UNMONITORED))
        result = json.loads(await run_skill("/investigate error", "/tmp"))
        assert result["subtype"] == "empty_output"
        assert result["success"] is False
        assert result["needs_retry"] is True
        assert result["retry_reason"] == RetryReason.RESUME
        assert result["stderr"] == "session dropped"


class TestRunSkillModel:
    """Tests for model parameter in run_skill and run_skill_retry."""

    _MOCK_STDOUT = (
        '{"type": "result", "subtype": "success", "is_error": false, '
        '"result": "done", "session_id": "s1"}'
    )

    # MOD_S1
    @pytest.mark.asyncio
    async def test_run_skill_passes_model_flag(self, tool_ctx):
        tool_ctx.runner.push(_make_result(0, self._MOCK_STDOUT, ""))
        await run_skill("/investigate error", "/tmp", model="sonnet")
        cmd = tool_ctx.runner.call_args_list[0][0]
        assert "--model" in cmd
        assert cmd[cmd.index("--model") + 1] == "sonnet"

    # MOD_S2
    @pytest.mark.asyncio
    async def test_run_skill_retry_passes_model_flag(self, tool_ctx):
        tool_ctx.runner.push(_make_result(0, self._MOCK_STDOUT, ""))
        await run_skill_retry("/investigate error", "/tmp", model="sonnet")
        cmd = tool_ctx.runner.call_args_list[0][0]
        assert "--model" in cmd
        assert cmd[cmd.index("--model") + 1] == "sonnet"

    # MOD_S3
    @pytest.mark.asyncio
    async def test_run_skill_no_model_flag_when_empty(self, tool_ctx):
        tool_ctx.runner.push(_make_result(0, self._MOCK_STDOUT, ""))
        await run_skill("/investigate error", "/tmp", model="")
        cmd = tool_ctx.runner.call_args_list[0][0]
        assert "--model" not in cmd


class TestResolveModel:
    """Tests for _resolve_model resolution chain."""

    @pytest.fixture(autouse=True)
    def _set_config(self, tool_ctx):
        self._tool_ctx = tool_ctx

    def _set_model_config(self, default=None, override=None):
        cfg = AutomationConfig(model=ModelConfig(default=default, override=override))
        self._tool_ctx.config = cfg

    # MOD_R1
    def test_resolve_model_override_wins(self):
        self._set_model_config(override="haiku")
        assert _resolve_model("sonnet", self._tool_ctx.config) == "haiku"

    # MOD_R2
    def test_resolve_model_step_model(self):
        self._set_model_config()
        assert _resolve_model("sonnet", self._tool_ctx.config) == "sonnet"

    # MOD_R3
    def test_resolve_model_config_default(self):
        self._set_model_config(default="haiku")
        assert _resolve_model("", self._tool_ctx.config) == "haiku"

    # MOD_R4
    def test_resolve_model_nothing_set(self):
        self._set_model_config()
        assert _resolve_model("", self._tool_ctx.config) is None


class TestBuildSkillResultTokenUsage:
    """token_usage field in _build_skill_result output."""

    def _make_ndjson(self, *, model: str = "claude-sonnet-4-6") -> str:
        """Build a two-line NDJSON with an assistant record and a result record with usage."""
        assistant = json.dumps(
            {
                "type": "assistant",
                "message": {
                    "model": model,
                    "usage": {
                        "input_tokens": 120,
                        "output_tokens": 45,
                        "cache_creation_input_tokens": 8,
                        "cache_read_input_tokens": 3,
                    },
                },
            }
        )
        result_rec = json.dumps(
            {
                "type": "result",
                "subtype": "success",
                "is_error": False,
                "result": "Task complete.",
                "session_id": "sess-abc",
                "usage": {
                    "input_tokens": 200,
                    "output_tokens": 80,
                    "cache_creation_input_tokens": 8,
                    "cache_read_input_tokens": 3,
                },
            }
        )
        return assistant + "\n" + result_rec

    def test_token_usage_included_when_present(self):
        """JSON response includes token_usage when session has usage data."""
        stdout = self._make_ndjson()
        result_obj = _make_result(0, stdout, "")
        response = json.loads(_build_skill_result(result_obj).to_json())
        assert "token_usage" in response
        usage = response["token_usage"]
        assert usage is not None
        assert usage["input_tokens"] == 200
        assert usage["output_tokens"] == 80
        assert usage["cache_creation_input_tokens"] == 8
        assert usage["cache_read_input_tokens"] == 3
        assert "model_breakdown" in usage
        assert "claude-sonnet-4-6" in usage["model_breakdown"]

    def test_token_usage_null_when_absent(self):
        """JSON response has token_usage: null when no usage data."""
        stdout = json.dumps(
            {
                "type": "result",
                "subtype": "success",
                "is_error": False,
                "result": "done",
                "session_id": "s1",
                # no usage field
            }
        )
        result_obj = _make_result(0, stdout, "")
        response = json.loads(_build_skill_result(result_obj).to_json())
        assert response["token_usage"] is None

    def test_stale_result_has_null_token_usage(self):
        """Stale termination produces null token_usage."""
        stale_result = SubprocessResult(
            returncode=-1,
            stdout="",
            stderr="",
            termination=TerminationReason.STALE,
            pid=1,
        )
        response = json.loads(_build_skill_result(stale_result).to_json())
        assert response["token_usage"] is None

    def test_timeout_result_has_null_token_usage(self):
        """Timeout termination produces null token_usage."""
        timeout_result = _make_timeout_result(stdout="", stderr="")
        response = json.loads(_build_skill_result(timeout_result).to_json())
        assert response["token_usage"] is None


class TestRetryResponseFieldsTokenUsage:
    """RETRY_RESPONSE_FIELDS includes token_usage."""

    def test_token_usage_in_fields(self):
        assert "token_usage" in RETRY_RESPONSE_FIELDS


class TestFailureCaptureInBuildSkillResult:
    """_build_skill_result() must capture failures into tool_ctx.audit."""

    def test_captures_non_zero_exit_code(self, tool_ctx):
        result = _make_result(returncode=1, stdout=_failed_session_json(), channel_confirmation=ChannelConfirmation.UNMONITORED)
        _build_skill_result(result, skill_command="/test:cmd", audit=tool_ctx.audit)
        assert len(tool_ctx.audit.get_report()) == 1

    def test_does_not_capture_clean_success(self, tool_ctx):
        result = _make_result(returncode=0, stdout=_success_session_json("done"))
        _build_skill_result(result, skill_command="/test:cmd", audit=tool_ctx.audit)
        assert tool_ctx.audit.get_report() == []

    def test_captured_record_has_correct_skill_command(self, tool_ctx):
        result = _make_result(returncode=1, stdout=_failed_session_json(), channel_confirmation=ChannelConfirmation.UNMONITORED)
        _build_skill_result(
            result, skill_command="/autoskillit:implement-worktree", audit=tool_ctx.audit
        )
        assert tool_ctx.audit.get_report()[0].skill_command == "/autoskillit:implement-worktree"

    def test_captured_record_has_timestamp(self, tool_ctx):
        from datetime import datetime

        result = _make_result(returncode=1, stdout=_failed_session_json(), channel_confirmation=ChannelConfirmation.UNMONITORED)
        _build_skill_result(result, skill_command="/test", audit=tool_ctx.audit)
        record = tool_ctx.audit.get_report()[0]
        assert record.timestamp  # non-empty ISO timestamp
        datetime.fromisoformat(record.timestamp)  # must parse as ISO

    def test_stale_termination_is_captured(self, tool_ctx):
        result = _make_result(returncode=0, termination_reason=TerminationReason.STALE)
        _build_skill_result(result, skill_command="/test", audit=tool_ctx.audit)
        report = tool_ctx.audit.get_report()
        assert len(report) == 1
        assert report[0].subtype == "stale"

    def test_needs_retry_is_captured(self, tool_ctx):
        result = _make_result(returncode=1, stdout=_context_exhausted_session_json())
        _build_skill_result(result, skill_command="/test", audit=tool_ctx.audit)
        report = tool_ctx.audit.get_report()
        assert len(report) == 1
        assert report[0].needs_retry is True

    def test_stderr_truncated_to_500_chars(self, tool_ctx):
        long_stderr = "e" * 2000
        result = _make_result(returncode=1, stderr=long_stderr, stdout=_failed_session_json(), channel_confirmation=ChannelConfirmation.UNMONITORED)
        _build_skill_result(result, skill_command="/test", audit=tool_ctx.audit)
        assert len(tool_ctx.audit.get_report()[0].stderr) <= 500


class TestRunSkillStepName:
    """step_name param drives token_log accumulation."""

    def _make_ndjson(self) -> str:
        result_rec = json.dumps(
            {
                "type": "result",
                "subtype": "success",
                "is_error": False,
                "result": "Task complete.",
                "session_id": "sess-abc",
                "usage": {
                    "input_tokens": 200,
                    "output_tokens": 80,
                    "cache_creation_input_tokens": 8,
                    "cache_read_input_tokens": 3,
                },
            }
        )
        return result_rec

    @pytest.mark.asyncio
    async def test_step_name_records_token_usage(self, tool_ctx):
        tool_ctx.runner.push(_make_result(returncode=0, stdout=self._make_ndjson()))
        await run_skill(
            skill_command="/autoskillit:investigate topic", cwd="/tmp", step_name="plan"
        )
        report = tool_ctx.token_log.get_report()
        assert len(report) == 1
        assert report[0]["step_name"] == "plan"
        assert report[0]["input_tokens"] == 200

    @pytest.mark.asyncio
    async def test_no_step_name_does_not_record(self, tool_ctx):
        tool_ctx.runner.push(_make_result(returncode=0, stdout=self._make_ndjson()))
        await run_skill(skill_command="/autoskillit:investigate topic", cwd="/tmp", step_name="")
        assert tool_ctx.token_log.get_report() == []

    @pytest.mark.asyncio
    async def test_null_token_usage_does_not_record(self, tool_ctx):
        # Return NDJSON with no usage field → token_usage will be null
        no_usage_ndjson = json.dumps(
            {
                "type": "result",
                "subtype": "success",
                "is_error": False,
                "result": "done",
                "session_id": "s1",
            }
        )
        tool_ctx.runner.push(_make_result(returncode=0, stdout=no_usage_ndjson))
        await run_skill(
            skill_command="/autoskillit:investigate topic", cwd="/tmp", step_name="plan"
        )
        assert tool_ctx.token_log.get_report() == []

    @pytest.mark.asyncio
    async def test_step_name_run_skill_retry(self, tool_ctx):
        tool_ctx.runner.push(_make_result(returncode=0, stdout=self._make_ndjson()))
        await run_skill_retry(
            skill_command="/autoskillit:investigate the test failures",
            cwd="/tmp",
            step_name="implement",
        )
        report = tool_ctx.token_log.get_report()
        assert len(report) == 1
        assert report[0]["step_name"] == "implement"
        assert report[0]["input_tokens"] == 200


class TestGatedToolObservability:
    """Each gated tool binds structlog contextvars and calls ctx.info/ctx.error."""

    @pytest.fixture
    def mock_ctx(self):
        """AsyncMock ctx for verifying ctx.info/ctx.error calls."""
        ctx = AsyncMock()
        ctx.info = AsyncMock()
        ctx.error = AsyncMock()
        return ctx

    @pytest.mark.asyncio
    async def test_run_cmd_binds_tool_contextvar_and_calls_ctx_info(self, tool_ctx, mock_ctx):
        """run_cmd binds tool='run_cmd' contextvar and calls ctx.info on success."""
        tool_ctx.runner.push(_make_result(0, "ok\n", ""))
        with structlog.testing.capture_logs(
            processors=[structlog.contextvars.merge_contextvars]
        ) as logs:
            await run_cmd(cmd="echo ok", cwd="/tmp", ctx=mock_ctx)
        assert any(entry.get("tool") == "run_cmd" for entry in logs)

    @pytest.mark.asyncio
    async def test_run_cmd_returns_failure_result_on_nonzero_exit(self, tool_ctx, mock_ctx):
        """run_cmd reports failure (success=false) when subprocess exits non-zero."""
        tool_ctx.runner.push(_make_result(1, "", "err"))
        result = json.loads(await run_cmd(cmd="false", cwd="/tmp", ctx=mock_ctx))
        assert result["success"] is False
        assert result["exit_code"] == 1

    @pytest.mark.asyncio
    async def test_run_python_binds_tool_contextvar_and_calls_ctx_info(self, tool_ctx, mock_ctx):
        """run_python binds tool='run_python' contextvar and calls ctx.info on success."""
        with structlog.testing.capture_logs(
            processors=[structlog.contextvars.merge_contextvars]
        ) as logs:
            await run_python(callable="json.dumps", args={"obj": 1}, ctx=mock_ctx)
        assert any(entry.get("tool") == "run_python" for entry in logs)

    @pytest.mark.asyncio
    async def test_run_python_returns_failure_result_on_bad_module(self, tool_ctx, mock_ctx):
        """run_python reports failure (success=false) when callable import fails."""
        result = json.loads(await run_python(callable="nonexistent.module.func", ctx=mock_ctx))
        assert result["success"] is False

    @pytest.mark.asyncio
    async def test_run_skill_binds_tool_contextvar_and_calls_ctx_info(self, tool_ctx, mock_ctx):
        """run_skill binds tool='run_skill' contextvar and calls ctx.info on success."""
        tool_ctx.runner.push(
            _make_result(
                0,
                '{"type": "result", "subtype": "success", "is_error": false,'
                ' "result": "done", "session_id": "s1"}',
                "",
            )
        )
        with structlog.testing.capture_logs(
            processors=[structlog.contextvars.merge_contextvars]
        ) as logs:
            await run_skill("/autoskillit:investigate task", "/tmp", ctx=mock_ctx)
        assert any(entry.get("tool") == "run_skill" for entry in logs)

    @pytest.mark.asyncio
    async def test_run_skill_returns_failure_result_on_error_output(self, tool_ctx, mock_ctx):
        """run_skill reports failure (success=false) when headless session fails."""
        tool_ctx.runner.push(
            _make_result(
                1,
                '{"type": "result", "subtype": "error", "is_error": true,'
                ' "result": "failed", "session_id": "s1"}',
                "",
                channel_confirmation=ChannelConfirmation.UNMONITORED,
            )
        )
        result = json.loads(await run_skill("/autoskillit:investigate task", "/tmp", ctx=mock_ctx))
        assert result["success"] is False

    @pytest.mark.asyncio
    async def test_run_skill_retry_binds_tool_contextvar_and_calls_ctx_info(
        self, tool_ctx, mock_ctx
    ):
        """run_skill_retry binds tool='run_skill_retry' contextvar and calls ctx.info."""
        tool_ctx.runner.push(
            _make_result(
                0,
                '{"type": "result", "subtype": "success", "is_error": false,'
                ' "result": "done", "session_id": "s1"}',
                "",
            )
        )
        with structlog.testing.capture_logs(
            processors=[structlog.contextvars.merge_contextvars]
        ) as logs:
            await run_skill_retry("/autoskillit:investigate task", "/tmp", ctx=mock_ctx)
        assert any(entry.get("tool") == "run_skill_retry" for entry in logs)

    @pytest.mark.asyncio
    async def test_run_skill_retry_returns_failure_result_on_error_output(
        self, tool_ctx, mock_ctx
    ):
        """run_skill_retry reports failure (success=false) when headless session fails."""
        tool_ctx.runner.push(
            _make_result(
                1,
                '{"type": "result", "subtype": "error", "is_error": true,'
                ' "result": "failed", "session_id": "s1"}',
                "",
                channel_confirmation=ChannelConfirmation.UNMONITORED,
            )
        )
        result = json.loads(
            await run_skill_retry("/autoskillit:investigate task", "/tmp", ctx=mock_ctx)
        )
        assert result["success"] is False


class TestNotifyHelper:
    """Unit tests for the centralized _notify() notification helper."""

    @pytest.mark.asyncio
    async def test_notify_raises_value_error_for_reserved_key_name(self):
        """The 'name' key that caused the original bug must be rejected."""
        from autoskillit.server.helpers import _notify

        ctx = AsyncMock()
        ctx.info = AsyncMock()
        with pytest.raises(ValueError, match="reserved LogRecord"):
            await _notify(
                ctx,
                "info",
                "migrate_recipe: foo",
                "autoskillit.migrate_recipe",
                extra={"name": "foo"},
            )
        ctx.info.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_notify_raises_for_all_reserved_keys(self):
        """Every key in RESERVED_LOG_RECORD_KEYS must be rejected."""
        from autoskillit.core.types import RESERVED_LOG_RECORD_KEYS
        from autoskillit.server.helpers import _notify

        ctx = AsyncMock()
        ctx.info = AsyncMock()
        for reserved_key in RESERVED_LOG_RECORD_KEYS:
            with pytest.raises(ValueError, match="reserved LogRecord"):
                await _notify(ctx, "info", "msg", "logger", extra={reserved_key: "value"})

    @pytest.mark.asyncio
    async def test_notify_accepts_safe_key_recipe_name(self):
        """'recipe_name' (the corrected key for migrate_recipe) must be accepted."""
        from autoskillit.server.helpers import _notify

        ctx = AsyncMock()
        ctx.info = AsyncMock()
        await _notify(
            ctx,
            "info",
            "migrate_recipe: foo",
            "autoskillit.migrate_recipe",
            extra={"recipe_name": "foo"},
        )
        ctx.info.assert_awaited_once_with(
            "migrate_recipe: foo",
            logger_name="autoskillit.migrate_recipe",
            extra={"recipe_name": "foo"},
        )

    @pytest.mark.asyncio
    async def test_notify_accepts_none_extra(self):
        from autoskillit.server.helpers import _notify

        ctx = AsyncMock()
        ctx.info = AsyncMock()
        await _notify(ctx, "info", "msg", "logger")  # no extra
        ctx.info.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_notify_accepts_empty_extra(self):
        from autoskillit.server.helpers import _notify

        ctx = AsyncMock()
        ctx.info = AsyncMock()
        await _notify(ctx, "info", "msg", "logger", extra={})
        ctx.info.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_notify_swallows_attribute_error_from_ctx(self):
        """AttributeError from ctx.info (e.g. _CurrentContext sentinel) is swallowed."""
        from autoskillit.server.helpers import _notify

        ctx = AsyncMock()
        ctx.info = AsyncMock(side_effect=AttributeError("no info"))
        # Must not raise
        await _notify(ctx, "info", "msg", "logger", extra={"cwd": "/tmp"})

    @pytest.mark.asyncio
    async def test_notify_swallows_runtime_error_from_ctx(self):
        """RuntimeError from ctx.info (no active MCP session) is swallowed."""
        from autoskillit.server.helpers import _notify

        ctx = AsyncMock()
        ctx.info = AsyncMock(side_effect=RuntimeError("session not available"))
        await _notify(ctx, "info", "msg", "logger", extra={"cwd": "/tmp"})

    @pytest.mark.asyncio
    async def test_notify_swallows_key_error_from_ctx(self):
        """KeyError from FastMCP's stdlib logging path is swallowed."""
        from autoskillit.server.helpers import _notify

        ctx = AsyncMock()
        ctx.info = AsyncMock(side_effect=KeyError("Attempt to overwrite 'name' in LogRecord"))
        await _notify(ctx, "info", "msg", "logger", extra={"cwd": "/tmp"})

    @pytest.mark.asyncio
    async def test_notify_dispatches_error_level(self):
        from autoskillit.server.helpers import _notify

        ctx = AsyncMock()
        ctx.error = AsyncMock()
        await _notify(
            ctx,
            "error",
            "run_cmd failed",
            "autoskillit.run_cmd",
            extra={"exit_code": 1},
        )
        ctx.error.assert_awaited_once_with(
            "run_cmd failed",
            logger_name="autoskillit.run_cmd",
            extra={"exit_code": 1},
        )


class TestStalePathStdoutCheck:
    """STALE termination recovers from stdout when a valid result record is present."""

    def _make_stale_result(self, stdout: str, returncode: int = -15) -> SubprocessResult:
        return SubprocessResult(
            returncode=returncode,
            stdout=stdout,
            stderr="",
            termination=TerminationReason.STALE,
            pid=12345,
        )

    def test_stale_kill_with_completed_result_in_stdout_is_success(self):
        """Session wrote a valid type=result record before going stale — should recover."""
        valid_completed_jsonl = json.dumps(
            {
                "type": "result",
                "subtype": "success",
                "is_error": False,
                "result": "Task completed successfully.",
                "session_id": "sess-stale-recovery",
            }
        )
        result_obj = self._make_stale_result(stdout=valid_completed_jsonl)
        parsed = json.loads(_build_skill_result(result_obj).to_json())
        assert parsed["success"] is True
        assert parsed["subtype"] == "recovered_from_stale"

    def test_stale_with_empty_stdout_returns_failure(self):
        """Stale session with no stdout — original failure response preserved."""
        result_obj = self._make_stale_result(stdout="")
        parsed = json.loads(_build_skill_result(result_obj).to_json())
        assert parsed["success"] is False
        assert parsed["subtype"] == "stale"

    def test_stale_with_error_result_returns_failure(self):
        """Stale session where the result record has is_error=True — not recovered."""
        error_jsonl = json.dumps(
            {
                "type": "result",
                "subtype": "error_during_execution",
                "is_error": True,
                "result": "Tool call failed.",
                "session_id": "sess-err",
            }
        )
        result_obj = self._make_stale_result(stdout=error_jsonl)
        parsed = json.loads(_build_skill_result(result_obj).to_json())
        assert parsed["success"] is False
        assert parsed["subtype"] == "stale"


class TestBuildSkillResultDataConfirmedPropagation:
    """_build_skill_result propagates data_confirmed for provenance bypass."""

    def test_stale_recovery_propagates_data_confirmed(self):
        """STALE recovery with data_confirmed=False engages provenance bypass."""
        result = _make_result(
            stdout="",
            termination_reason=TerminationReason.STALE,
            channel_confirmation=ChannelConfirmation.CHANNEL_B,
        )
        skill_result = _build_skill_result(
            result,
            completion_marker="%%ORDER_UP%%",
            skill_command="cmd",
            audit=None,
        )
        # Provenance bypass should fire in STALE recovery; success=True
        assert skill_result.success is True  # FAILS before fix: False

    def test_stale_recovery_data_confirmed_true_preserves_existing_behavior(self):
        """STALE with empty stdout and data_confirmed=True (default) stays False."""
        result = _make_result(
            stdout="",
            termination_reason=TerminationReason.STALE,
            channel_confirmation=ChannelConfirmation.CHANNEL_A,
        )
        skill_result = _build_skill_result(
            result,
            completion_marker="%%ORDER_UP%%",
            skill_command="cmd",
            audit=None,
        )
        assert skill_result.success is False
        assert skill_result.subtype == "stale"

    def test_completed_empty_result_data_confirmed_false_produces_success(self):
        """COMPLETED with empty stdout and data_confirmed=False uses provenance bypass."""
        result = _make_result(
            stdout='{"type":"result","subtype":"success","result":"","is_error":false,'
            '"session_id":"s1"}',
            returncode=-15,
            termination_reason=TerminationReason.COMPLETED,
            channel_confirmation=ChannelConfirmation.CHANNEL_B,
        )
        skill_result = _build_skill_result(
            result,
            completion_marker="%%ORDER_UP%%",
            skill_command="cmd",
            audit=None,
        )
        assert skill_result.success is True  # FAILS before fix: False
        assert skill_result.needs_retry is False  # FAILS before fix: True

    def test_completed_empty_result_data_confirmed_true_is_still_retriable(self):
        """COMPLETED with empty result and data_confirmed=True remains a retriable anomaly."""
        result = _make_result(
            stdout='{"type":"result","subtype":"success","result":"","is_error":false,'
            '"session_id":"s1"}',
            returncode=-15,
            termination_reason=TerminationReason.COMPLETED,
            channel_confirmation=ChannelConfirmation.CHANNEL_A,
        )
        skill_result = _build_skill_result(
            result,
            completion_marker="%%ORDER_UP%%",
            skill_command="cmd",
            audit=None,
        )
        assert skill_result.success is False
        assert skill_result.needs_retry is True


def test_context_exhaustion_marker_is_used_in_detection():
    """_is_context_exhausted() uses the CONTEXT_EXHAUSTION_MARKER constant."""
    from autoskillit.execution.session import ClaudeSessionResult

    session = ClaudeSessionResult(
        subtype="success",
        is_error=True,
        result=CONTEXT_EXHAUSTION_MARKER,
        session_id="s1",
    )
    assert session._is_context_exhausted() is True


@pytest.mark.asyncio
async def test_tools_execution_routes_through_executor(tool_ctx, monkeypatch) -> None:
    """run_skill routes through ctx.executor.run(), not run_headless_core directly."""
    from autoskillit.core import SkillResult

    calls = []

    class MockExecutor:
        async def run(
            self,
            skill_command: str,
            cwd: str,
            *,
            model: str = "",
            step_name: str = "",
            add_dir: str = "",
            timeout: float | None = None,
            stale_threshold: float | None = None,
        ) -> SkillResult:
            calls.append((skill_command, cwd))
            return SkillResult(
                success=True,
                result="ok",
                session_id="",
                subtype="success",
                is_error=False,
                exit_code=0,
                needs_retry=False,
                retry_reason="none",
                stderr="",
                token_usage=None,
            )

    tool_ctx.executor = MockExecutor()
    monkeypatch.setattr("autoskillit.server._ctx", tool_ctx)

    from autoskillit.server.tools_execution import run_skill

    await run_skill("/test skill", "/tmp")
    assert calls == [("/test skill", "/tmp")]


class TestResponseFieldsAreTypeSafe:
    """Every discriminator field in MCP tool responses uses enum values."""

    @pytest.mark.asyncio
    async def test_retry_reason_is_enum_value(self, tool_ctx):
        stdout = json.dumps(
            {
                "type": "result",
                "subtype": "error_max_turns",
                "is_error": False,
                "session_id": "s1",
                "num_turns": 200,
                "errors": [],
            }
        )
        tool_ctx.runner.push(_make_result(1, stdout, ""))
        result = json.loads(await run_skill_retry("/retry-worktree plan.md", "/tmp"))
        assert result["retry_reason"] in {e.value for e in RetryReason}

    @pytest.mark.asyncio
    async def test_retry_reason_none_is_enum_value(self, tool_ctx):
        stdout = json.dumps(
            {
                "type": "result",
                "subtype": "success",
                "is_error": False,
                "result": "Done.",
                "session_id": "s1",
                "num_turns": 50,
            }
        )
        tool_ctx.runner.push(_make_result(0, stdout, ""))
        result = json.loads(await run_skill_retry("/retry-worktree plan.md", "/tmp"))
        assert result["retry_reason"] in {e.value for e in RetryReason}
