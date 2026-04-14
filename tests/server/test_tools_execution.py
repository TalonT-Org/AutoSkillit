"""Tests for autoskillit server execution tools."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock

import pytest
import structlog.contextvars
import structlog.testing

from autoskillit.config import (
    AutomationConfig,
    RunSkillConfig,
)
from autoskillit.core.types import (
    ChannelConfirmation,
    RetryReason,
)
from autoskillit.execution.commands import _inject_completion_directive
from autoskillit.execution.headless import _session_log_dir
from autoskillit.server.helpers import (
    _check_dry_walkthrough,
)
from autoskillit.server.tools_execution import run_skill
from tests.conftest import _make_result

_SUCCESS_JSON = (
    '{"type": "result", "subtype": "success", "is_error": false,'
    ' "result": "done", "session_id": "s1"}'
)

# Deterministic UUID for tests that need to predict the per-invocation marker.
_DETERMINISTIC_HEX = "a1b2c3d4e5f6a7b890123456"
_DETERMINISTIC_MARKER = f"%%ORDER_UP::{_DETERMINISTIC_HEX[:8]}%%"


class _FixedUUID:
    hex = _DETERMINISTIC_HEX


def _patch_uuid4(monkeypatch):
    """Monkeypatch uuid4 to return a deterministic value for marker prediction."""
    monkeypatch.setattr("uuid.uuid4", lambda: _FixedUUID())


class TestRunSkillPluginDir:
    """T2: run_skill passes --plugin-dir to the claude command."""

    @pytest.mark.anyio
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
        # --output-format and stream-json must be present
        assert "--output-format" in cmd
        assert cmd[cmd.index("--output-format") + 1] == "stream-json"
        # cwd must propagate to the subprocess runner
        from pathlib import Path

        actual_cwd = tool_ctx.runner.call_args_list[0][1]
        assert actual_cwd == Path("/tmp"), f"Subprocess cwd mismatch: {actual_cwd} != /tmp"


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


class TestRunSkillPrefix:
    """run_skill passes prefixed command to subprocess."""

    @pytest.mark.anyio
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
        prompt_idx = cmd.index("--print") + 1 if "--print" in cmd else cmd.index("-p") + 1
        assert cmd[prompt_idx].startswith("Use /investigate error")
        # cwd must propagate to the subprocess runner
        from pathlib import Path

        actual_cwd = tool_ctx.runner.call_args_list[0][1]
        assert actual_cwd == Path("/tmp"), f"Subprocess cwd mismatch: {actual_cwd} != /tmp"

    @pytest.mark.anyio
    async def test_run_skill_rejects_prose_without_slash(self, tool_ctx):
        """FRICT-6-1: prose command without slash returns gate_error before reaching executor."""
        result = json.loads(await run_skill("Fix the authentication bug in main.py", "/tmp"))
        assert result["success"] is False
        assert result["is_error"] is True
        assert result["subtype"] == "gate_error"
        # executor must NOT have been called
        assert tool_ctx.runner.call_args_list == []

    @pytest.mark.anyio
    async def test_run_skill_rejects_empty_skill_command(self, tool_ctx):
        """FRICT-6-1: empty string returns gate_error without hitting executor."""
        result = json.loads(await run_skill("", "/tmp"))
        assert result["success"] is False
        assert result["is_error"] is True
        assert result["subtype"] == "gate_error"
        assert tool_ctx.runner.call_args_list == []

    @pytest.mark.anyio
    async def test_run_skill_rejects_whitespace_only(self, tool_ctx):
        """FRICT-6-1: whitespace-only command returns gate_error (strip before check)."""
        result = json.loads(await run_skill("   ", "/tmp"))
        assert result["success"] is False
        assert result["is_error"] is True
        assert result["subtype"] == "gate_error"
        assert tool_ctx.runner.call_args_list == []

    @pytest.mark.anyio
    async def test_run_skill_format_error_includes_slash_examples(self, tool_ctx):
        """FRICT-6-1: error message for invalid format includes concrete slash-command examples."""
        result = json.loads(await run_skill("investigate this bug", "/tmp"))
        assert result["success"] is False
        assert "/autoskillit:" in result["result"]
        assert "/" in result["result"]

    @pytest.mark.anyio
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
        prompt_idx = cmd.index("--print") + 1 if "--print" in cmd else cmd.index("-p") + 1
        assert "%%ORDER_UP::" in cmd[prompt_idx]
        # cwd must propagate to the subprocess runner
        from pathlib import Path

        actual_cwd = tool_ctx.runner.call_args_list[0][1]
        assert actual_cwd == Path("/tmp"), f"Subprocess cwd mismatch: {actual_cwd} != /tmp"


class TestValidateSkillCommand:
    """Unit tests for _validate_skill_command helper."""

    def test_returns_none_for_slash_command(self, tool_ctx):
        from autoskillit.server.helpers import _validate_skill_command

        assert _validate_skill_command("/autoskillit:investigate") is None

    def test_returns_none_for_bare_slash_command(self, tool_ctx):
        from autoskillit.server.helpers import _validate_skill_command

        assert _validate_skill_command("/audit-arch") is None

    def test_returns_error_json_for_prose(self, tool_ctx):
        from autoskillit.server.helpers import _validate_skill_command

        result = _validate_skill_command("Fix the bug")
        assert result is not None
        parsed = json.loads(result)
        assert parsed["success"] is False
        assert parsed["subtype"] == "gate_error"

    def test_returns_error_json_for_empty_string(self, tool_ctx):
        from autoskillit.server.helpers import _validate_skill_command

        result = _validate_skill_command("")
        assert result is not None

    def test_strips_whitespace_before_check(self, tool_ctx):
        from autoskillit.server.helpers import _validate_skill_command

        # Leading whitespace before slash → valid
        assert _validate_skill_command("  /autoskillit:investigate") is None
        # Leading whitespace before prose → invalid
        result = _validate_skill_command("  investigate bug")
        assert result is not None


class TestDryWalkthroughGateWithPrefix:
    """Dry-walkthrough gate still receives raw command before prefix is applied."""

    @pytest.mark.anyio
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
    """run_skill uses configurable timeouts."""

    @pytest.mark.anyio
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

    @pytest.mark.anyio
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
        tool_ctx.runner.push(_make_result(0, _SUCCESS_JSON, ""))
        await run_skill("/investigate something", "/tmp")
        cmd, _cwd, _timeout, kwargs = tool_ctx.runner.call_args_list[0]
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
        tool_ctx.runner.push(_make_result(0, _SUCCESS_JSON, ""))
        await run_skill("/investigate something", "/tmp")
        cmd, _cwd, _timeout, kwargs = tool_ctx.runner.call_args_list[0]
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
        tool_ctx.runner.push(_make_result(0, _SUCCESS_JSON, ""))
        await run_skill("/investigate something", "/tmp")
        cmd, _cwd, _timeout, kwargs = tool_ctx.runner.call_args_list[0]
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


class TestRunSkillFailurePaths:
    """run_skill surfaces session outcome on failure."""

    @pytest.mark.anyio
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

    @pytest.mark.anyio
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
        assert result["subtype"] == "missing_completion_marker"
        assert result["cli_subtype"] == "success"

    @pytest.mark.anyio
    async def test_handles_empty_stdout(self, tool_ctx):
        """run_skill returns error result when stdout is empty."""
        tool_ctx.runner.push(
            _make_result(1, "", "segfault", channel_confirmation=ChannelConfirmation.UNMONITORED)
        )
        result = json.loads(await run_skill("/investigate error", "/tmp"))
        assert result["exit_code"] == 1
        assert result["is_error"] is True
        assert result["subtype"] == "empty_output"
        assert result["success"] is False

    @pytest.mark.anyio
    async def test_empty_stdout_exit_zero_is_retriable(self, tool_ctx):
        """Infrastructure failure (empty stdout, exit 0) is retriable with stderr."""
        tool_ctx.runner.push(
            _make_result(
                0, "", "session dropped", channel_confirmation=ChannelConfirmation.UNMONITORED
            )
        )
        result = json.loads(await run_skill("/investigate error", "/tmp"))
        assert result["subtype"] == "empty_output"
        assert result["success"] is False
        assert result["needs_retry"] is True
        assert result["retry_reason"] == RetryReason.EMPTY_OUTPUT
        assert result["stderr"] == "session dropped"


class TestRunSkillModel:
    """Tests for model parameter in run_skill."""

    _MOCK_STDOUT = (
        '{"type": "result", "subtype": "success", "is_error": false, '
        '"result": "done", "session_id": "s1"}'
    )

    # MOD_S1
    @pytest.mark.anyio
    async def test_run_skill_passes_model_flag(self, tool_ctx):
        tool_ctx.runner.push(_make_result(0, self._MOCK_STDOUT, ""))
        await run_skill("/investigate error", "/tmp", model="sonnet")
        cmd = tool_ctx.runner.call_args_list[0][0]
        assert "--model" in cmd
        assert cmd[cmd.index("--model") + 1] == "sonnet"

    # MOD_S3
    @pytest.mark.anyio
    async def test_run_skill_no_model_flag_when_empty(self, tool_ctx):
        tool_ctx.config.model.default = ""  # ← add this line
        tool_ctx.runner.push(_make_result(0, self._MOCK_STDOUT, ""))
        await run_skill("/investigate error", "/tmp", model="")
        cmd = tool_ctx.runner.call_args_list[0][0]
        assert "--model" not in cmd


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

    @pytest.mark.anyio
    async def test_step_name_records_token_usage(self, tool_ctx):
        tool_ctx.runner.push(_make_result(returncode=0, stdout=self._make_ndjson()))
        await run_skill(
            skill_command="/autoskillit:investigate topic", cwd="/tmp", step_name="plan"
        )
        report = tool_ctx.token_log.get_report()
        assert len(report) == 1
        assert report[0]["step_name"] == "plan"
        assert report[0]["input_tokens"] == 200

    @pytest.mark.anyio
    async def test_no_step_name_does_not_record(self, tool_ctx):
        tool_ctx.runner.push(_make_result(returncode=0, stdout=self._make_ndjson()))
        await run_skill(skill_command="/autoskillit:investigate topic", cwd="/tmp", step_name="")
        assert tool_ctx.token_log.get_report() == []

    @pytest.mark.anyio
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

    @pytest.mark.anyio
    async def test_step_name_run_skill_long_running(self, tool_ctx):
        """run_skill accumulates token usage by step_name (run_skill_retry test replacement)."""
        tool_ctx.runner.push(_make_result(returncode=0, stdout=self._make_ndjson()))
        await run_skill(
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

    @pytest.mark.anyio
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

    @pytest.mark.anyio
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


class TestNotifyHelper:
    """Unit tests for the centralized _notify() notification helper."""

    @pytest.mark.anyio
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

    @pytest.mark.anyio
    async def test_notify_raises_for_all_reserved_keys(self):
        """Every key in RESERVED_LOG_RECORD_KEYS must be rejected."""
        from autoskillit.core.types import RESERVED_LOG_RECORD_KEYS
        from autoskillit.server.helpers import _notify

        ctx = AsyncMock()
        ctx.info = AsyncMock()
        for reserved_key in RESERVED_LOG_RECORD_KEYS:
            with pytest.raises(ValueError, match="reserved LogRecord"):
                await _notify(ctx, "info", "msg", "logger", extra={reserved_key: "value"})

    @pytest.mark.anyio
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

    @pytest.mark.anyio
    async def test_notify_accepts_none_extra(self):
        from autoskillit.server.helpers import _notify

        ctx = AsyncMock()
        ctx.info = AsyncMock()
        await _notify(ctx, "info", "msg", "logger")  # no extra
        ctx.info.assert_awaited_once()

    @pytest.mark.anyio
    async def test_notify_accepts_empty_extra(self):
        from autoskillit.server.helpers import _notify

        ctx = AsyncMock()
        ctx.info = AsyncMock()
        await _notify(ctx, "info", "msg", "logger", extra={})
        ctx.info.assert_awaited_once()

    @pytest.mark.anyio
    async def test_notify_swallows_attribute_error_from_ctx(self):
        """Contract: must not raise even when ctx.info raises AttributeError
        (e.g. _CurrentContext sentinel). Test completion is the assertion."""
        from autoskillit.server.helpers import _notify

        ctx = AsyncMock()
        ctx.info = AsyncMock(side_effect=AttributeError("no info"))
        # Must not raise
        await _notify(ctx, "info", "msg", "logger", extra={"cwd": "/tmp"})

    @pytest.mark.anyio
    async def test_notify_swallows_runtime_error_from_ctx(self):
        """Contract: must not raise even when ctx.info raises RuntimeError
        (no active MCP session). Test completion is the assertion."""
        from autoskillit.server.helpers import _notify

        ctx = AsyncMock()
        ctx.info = AsyncMock(side_effect=RuntimeError("session not available"))
        await _notify(ctx, "info", "msg", "logger", extra={"cwd": "/tmp"})

    @pytest.mark.anyio
    async def test_notify_swallows_key_error_from_ctx(self):
        """Contract: must not raise even when ctx.info raises KeyError
        (FastMCP stdlib logging path). Test completion is the assertion."""
        from autoskillit.server.helpers import _notify

        ctx = AsyncMock()
        ctx.info = AsyncMock(side_effect=KeyError("Attempt to overwrite 'name' in LogRecord"))
        await _notify(ctx, "info", "msg", "logger", extra={"cwd": "/tmp"})

    @pytest.mark.anyio
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


@pytest.mark.anyio
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
            add_dirs=(),
            kitchen_id: str = "",
            order_id: str = "",
            timeout: float | None = None,
            stale_threshold: float | None = None,
            idle_output_timeout: float | None = None,
            expected_output_patterns: tuple[str, ...] | list[str] = (),
            write_behavior=None,
            completion_marker: str = "",
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


@pytest.mark.anyio
async def test_run_skill_passes_validated_add_dirs(tool_ctx, monkeypatch) -> None:
    """run_skill passes ValidatedAddDir instances (not raw strings) as add_dirs."""
    from autoskillit.core import SkillResult, ValidatedAddDir

    captured: dict = {}

    class MockExecutor:
        async def run(
            self,
            skill_command: str,
            cwd: str,
            *,
            model: str = "",
            step_name: str = "",
            add_dirs=(),
            kitchen_id: str = "",
            order_id: str = "",
            timeout: float | None = None,
            stale_threshold: float | None = None,
            idle_output_timeout: float | None = None,
            expected_output_patterns: tuple[str, ...] | list[str] = (),
            write_behavior=None,
            completion_marker: str = "",
        ) -> SkillResult:
            captured["add_dirs"] = add_dirs
            captured["cwd"] = cwd
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
    # All add_dirs must be ValidatedAddDir instances
    assert len(captured["add_dirs"]) >= 1
    assert all(isinstance(d, ValidatedAddDir) for d in captured["add_dirs"])
    # Must not include raw skills_extended/ path
    from autoskillit.workspace.skills import bundled_skills_extended_dir

    skills_ext = str(bundled_skills_extended_dir())
    add_dir_paths = [d.path for d in captured["add_dirs"]]
    assert skills_ext not in add_dir_paths


@pytest.mark.anyio
async def test_run_skill_calls_session_skill_manager_init_session(tool_ctx, monkeypatch) -> None:
    """run_skill routes through session_skill_manager.init_session() for add_dirs."""
    from unittest.mock import MagicMock

    from autoskillit.core import SkillResult, ValidatedAddDir

    # Create a spy on init_session
    fake_validated = ValidatedAddDir(path="/fake/session/dir")
    mock_ssm = MagicMock()
    mock_ssm.init_session.return_value = fake_validated
    tool_ctx.session_skill_manager = mock_ssm

    captured: dict = {}

    class MockExecutor:
        async def run(self, skill_command, cwd, *, add_dirs=(), **kwargs) -> SkillResult:
            captured["add_dirs"] = add_dirs
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

    # init_session was called with cook_session=False (headless, not cook)
    mock_ssm.init_session.assert_called_once()
    call_kwargs = mock_ssm.init_session.call_args
    assert call_kwargs.kwargs.get("cook_session") is False

    # The returned ValidatedAddDir is in add_dirs
    assert fake_validated in captured["add_dirs"]


@pytest.mark.anyio
async def test_run_skill_activates_deps_for_tier3_target(tool_ctx, monkeypatch) -> None:
    """run_skill calls activate_skill_deps even when target is tier3 (not in tier2 list)."""
    from unittest.mock import MagicMock

    from autoskillit.core import SkillResult, ValidatedAddDir

    fake_validated = ValidatedAddDir(path="/fake/session/dir")
    mock_ssm = MagicMock()
    mock_ssm.init_session.return_value = fake_validated
    tool_ctx.session_skill_manager = mock_ssm

    # Set up skill_resolver to produce a resolved name
    mock_resolver = MagicMock()
    mock_resolver.resolve.return_value = MagicMock(source=MagicMock(value="bundled_extended"))
    tool_ctx.skill_resolver = mock_resolver

    class MockExecutor:
        async def run(self, skill_command, cwd, *, add_dirs=(), **kwargs) -> SkillResult:
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

    # Use a tier3 skill name
    await run_skill("/open-pr", "/tmp")

    # activate_skill_deps must have been called regardless of tier
    mock_ssm.activate_skill_deps.assert_called_once()


@pytest.mark.anyio
async def test_run_skill_result_includes_order_id_when_passed(tool_ctx, monkeypatch) -> None:
    """run_skill injects order_id into the result JSON when order_id is non-empty."""
    import json as _json

    from autoskillit.core import SkillResult

    class MockExecutor:
        async def run(self, skill_command, cwd, *, add_dirs=(), **kwargs) -> SkillResult:
            return SkillResult(
                success=True,
                result="done",
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

    result_json = await run_skill("/test skill", "/tmp", order_id="issue-185")
    data = _json.loads(result_json)
    assert data.get("order_id") == "issue-185"


@pytest.mark.anyio
async def test_run_skill_result_no_order_id_field_when_empty(tool_ctx, monkeypatch) -> None:
    """run_skill does NOT inject order_id into result JSON when order_id is empty."""
    import json as _json

    from autoskillit.core import SkillResult

    class MockExecutor:
        async def run(self, skill_command, cwd, *, add_dirs=(), **kwargs) -> SkillResult:
            return SkillResult(
                success=True,
                result="done",
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

    result_json = await run_skill("/test skill", "/tmp")  # no order_id
    data = _json.loads(result_json)
    assert "order_id" not in data


class TestHeadlessGateEnforcement:
    """T_HGE: run_skill, run_cmd, run_python each return headless_error
    when the session is running with AUTOSKILLIT_HEADLESS=1.

    The gate is open (tool_ctx default), so _require_enabled() passes.
    _require_not_headless() fires first and returns subtype='headless_error'.
    """

    @pytest.fixture(autouse=True)
    def _set_headless_env(self, monkeypatch):
        monkeypatch.setenv("AUTOSKILLIT_HEADLESS", "1")

    @pytest.mark.anyio
    async def test_run_skill_blocked_in_headless_session(self, tool_ctx):
        """run_skill returns headless_error when AUTOSKILLIT_HEADLESS=1."""
        result = json.loads(await run_skill("/autoskillit:investigate some-error", "/tmp"))
        assert result["subtype"] == "headless_error"


class TestResponseFieldsAreTypeSafe:
    """Every discriminator field in MCP tool responses uses enum values."""

    @pytest.mark.anyio
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
        result = json.loads(await run_skill("/retry-worktree plan.md", "/tmp"))
        assert result["retry_reason"] in {e.value for e in RetryReason}

    @pytest.mark.anyio
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
        result = json.loads(await run_skill("/retry-worktree plan.md", "/tmp"))
        assert result["retry_reason"] in {e.value for e in RetryReason}


class TestRunSkillTiming:
    """run_skill accumulates wall-clock timing when step_name is provided."""

    @pytest.mark.anyio
    async def test_run_skill_records_timing_via_step_name(self, tool_ctx):
        tool_ctx.runner.push(_make_result(0, _SUCCESS_JSON, ""))
        await run_skill("/investigate foo", "/tmp", step_name="implement")
        report = tool_ctx.timing_log.get_report()
        assert len(report) == 1
        assert report[0]["step_name"] == "implement"
        assert report[0]["invocation_count"] == 1

    @pytest.mark.anyio
    async def test_run_skill_empty_step_name_skips_timing(self, tool_ctx):
        tool_ctx.runner.push(_make_result(0, _SUCCESS_JSON, ""))
        await run_skill("/investigate foo", "/tmp")
        assert tool_ctx.timing_log.get_report() == []


class TestRunHeadlessCoreFlushTelemetry:
    """flush_session_log receives telemetry kwargs when step_name is provided."""

    def _make_ndjson_with_usage(self) -> str:
        asst = json.dumps(
            {
                "type": "assistant",
                "message": {
                    "usage": {
                        "input_tokens": 200,
                        "output_tokens": 100,
                        "cache_creation_input_tokens": 0,
                        "cache_read_input_tokens": 0,
                    }
                },
                "model": "claude-opus-4-6",
            }
        )
        result = json.dumps(
            {
                "type": "result",
                "subtype": "success",
                "is_error": False,
                "result": "done",
                "session_id": "s1",
                "usage": {
                    "input_tokens": 200,
                    "output_tokens": 100,
                    "cache_creation_input_tokens": 0,
                    "cache_read_input_tokens": 0,
                },
            }
        )
        return asst + "\n" + result

    @pytest.mark.anyio
    async def test_passes_step_telemetry_to_flush(self, tool_ctx, monkeypatch):
        """flush_session_log is called with step_name, token_usage, and timing_seconds."""
        import autoskillit.execution.session_log as sl_mod

        calls = []

        def mock_flush(**kwargs):
            calls.append(kwargs)

        monkeypatch.setattr(sl_mod, "flush_session_log", mock_flush)
        tool_ctx.runner.push(_make_result(returncode=0, stdout=self._make_ndjson_with_usage()))
        await run_skill("/investigate foo", "/tmp", step_name="implement")
        assert len(calls) == 1
        assert calls[0]["step_name"] == "implement"
        assert calls[0]["token_usage"] is not None
        assert calls[0]["timing_seconds"] is not None

    @pytest.mark.anyio
    async def test_flush_session_log_session_id_matches_returned_skill_result(
        self, tool_ctx, monkeypatch
    ):
        """flush_session_log receives the same session_id as the returned SkillResult."""
        import autoskillit.execution.session_log as sl_mod
        from autoskillit.core.types import SubprocessResult, TerminationReason

        calls = []

        def mock_flush(**kwargs):
            calls.append(kwargs)

        monkeypatch.setattr(sl_mod, "flush_session_log", mock_flush)
        # Stale result with session_id resolved from Channel B
        stale_result = SubprocessResult(
            returncode=-1,
            stdout="",
            stderr="",
            termination=TerminationReason.STALE,
            pid=12345,
            session_id="test-uuid-coherence-check",
        )
        tool_ctx.runner.push(stale_result)
        result_json = json.loads(
            await run_skill("/investigate foo", "/tmp", step_name="implement")
        )
        assert len(calls) == 1
        # flush and returned SkillResult must carry the same session_id
        assert calls[0]["session_id"] == result_json["session_id"]
        assert result_json["session_id"] != ""

    @pytest.mark.anyio
    async def test_flushes_on_success_when_step_name_set(self, tool_ctx, monkeypatch):
        """Successful sessions without proc_snapshots still flush when step_name is provided."""
        import autoskillit.execution.session_log as sl_mod

        calls = []

        def mock_flush(**kwargs):
            calls.append(kwargs)

        monkeypatch.setattr(sl_mod, "flush_session_log", mock_flush)
        tool_ctx.runner.push(_make_result(returncode=0, stdout=_SUCCESS_JSON))
        await run_skill("/investigate foo", "/tmp", step_name="plan")
        assert len(calls) == 1

    @pytest.mark.anyio
    async def test_records_timing_in_timing_log(self, tool_ctx):
        """ctx.timing_log.record() is called with step_name and computed timing_seconds."""
        tool_ctx.runner.push(_make_result(returncode=0, stdout=_SUCCESS_JSON))
        await run_skill("/investigate foo", "/tmp", step_name="plan")
        report = tool_ctx.timing_log.get_report()
        assert len(report) == 1
        assert report[0]["step_name"] == "plan"
        assert report[0]["total_seconds"] >= 0.0


class TestRunSkillCwdValidation:
    """run_skill rejects non-empty relative cwd at the boundary."""

    @pytest.mark.anyio
    async def test_run_skill_rejects_relative_cwd(self, tool_ctx):
        """Non-empty relative cwd is rejected immediately with a clear diagnostic."""
        result = json.loads(
            await run_skill(
                "/autoskillit:retry-worktree plan.md ../worktrees/impl-fix",
                cwd="../worktrees/impl-fix-20260316",
            )
        )
        assert result["success"] is False
        assert "cwd must be an absolute path" in result["error"]
        assert "../worktrees/impl-fix-20260316" in result["error"]
        assert tool_ctx.runner.call_args_list == []

    @pytest.mark.anyio
    async def test_run_skill_accepts_empty_cwd(self, tool_ctx, monkeypatch):
        """Empty cwd is accepted (some skills have no specific cwd requirement)."""
        _patch_uuid4(monkeypatch)
        marker = _DETERMINISTIC_MARKER
        success_json = (
            '{"type": "result", "subtype": "success", "is_error": false,'
            f' "result": "done {marker}", "session_id": "s1"}}'
        )
        tool_ctx.runner.push(_make_result(returncode=0, stdout=success_json))
        result = json.loads(await run_skill("/investigate foo", cwd=""))
        assert result["success"] is True

    @pytest.mark.anyio
    async def test_run_skill_accepts_absolute_cwd(self, tool_ctx, monkeypatch):
        """Absolute cwd passes the boundary check and proceeds normally."""
        _patch_uuid4(monkeypatch)
        marker = _DETERMINISTIC_MARKER
        success_json = (
            '{"type": "result", "subtype": "success", "is_error": false,'
            f' "result": "done {marker}", "session_id": "s1"}}'
        )
        tool_ctx.runner.push(_make_result(returncode=0, stdout=success_json))
        result = json.loads(await run_skill("/investigate foo", cwd="/tmp"))
        assert result["success"] is True


class TestRunSkillPerInvocationMarker:
    """Per-invocation completion markers are unique across run_skill calls."""

    @pytest.mark.anyio
    async def test_run_skill_markers_are_unique_per_invocation(self, tool_ctx):
        """Two run_skill calls must generate different completion_marker values."""
        success_json = (
            '{"type": "result", "subtype": "success", "is_error": false,'
            ' "result": "done", "session_id": "s1"}'
        )
        tool_ctx.runner.push(_make_result(returncode=0, stdout=success_json))
        tool_ctx.runner.push(_make_result(returncode=0, stdout=success_json))

        await run_skill("/investigate a", cwd="/tmp")
        await run_skill("/investigate b", cwd="/tmp")

        calls = tool_ctx.runner.call_args_list
        assert len(calls) >= 2
        marker1 = calls[0][3]["completion_marker"]
        marker2 = calls[1][3]["completion_marker"]
        assert marker1 != marker2
        assert "%%ORDER_UP::" in marker1
        assert "%%ORDER_UP::" in marker2


@pytest.mark.anyio
async def test_run_skill_passes_allow_only_to_init_session(tool_ctx, monkeypatch) -> None:
    """run_skill computes the closure for the resolved target and forwards it as allow_only."""
    from unittest.mock import MagicMock

    from autoskillit.core import SkillResult, ValidatedAddDir

    fake_validated = ValidatedAddDir(path="/fake/session/dir")
    expected_closure = frozenset({"investigate", "mermaid"})

    mock_ssm = MagicMock()
    mock_ssm.init_session.return_value = fake_validated
    mock_ssm.compute_skill_closure.return_value = expected_closure
    tool_ctx.session_skill_manager = mock_ssm

    mock_resolver = MagicMock()
    mock_resolver.resolve.return_value = MagicMock(source=MagicMock(value="bundled_extended"))
    tool_ctx.skill_resolver = mock_resolver

    class MockExecutor:
        async def run(self, skill_command, cwd, *, add_dirs=(), **kwargs) -> SkillResult:
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

    await run_skill("/autoskillit:investigate the bug", "/tmp")

    mock_ssm.compute_skill_closure.assert_called_once_with("investigate")
    mock_ssm.init_session.assert_called_once()
    assert mock_ssm.init_session.call_args.kwargs.get("allow_only") == expected_closure


@pytest.mark.anyio
async def test_run_skill_no_target_skill_passes_none_allow_only(tool_ctx, monkeypatch) -> None:
    """When skill_resolver is unset, target_name is None and allow_only stays None."""
    from unittest.mock import MagicMock

    from autoskillit.core import SkillResult, ValidatedAddDir

    fake_validated = ValidatedAddDir(path="/fake/session/dir")
    mock_ssm = MagicMock()
    mock_ssm.init_session.return_value = fake_validated
    tool_ctx.session_skill_manager = mock_ssm
    tool_ctx.skill_resolver = None  # disables resolve_target_skill

    class MockExecutor:
        async def run(self, skill_command, cwd, *, add_dirs=(), **kwargs) -> SkillResult:
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

    mock_ssm.init_session.assert_called_once()
    assert mock_ssm.init_session.call_args.kwargs.get("allow_only") is None
    mock_ssm.compute_skill_closure.assert_not_called()


@pytest.mark.anyio
async def test_run_skill_make_plan_closure_includes_arch_lens_pack(tool_ctx, monkeypatch) -> None:
    """End-to-end: /make-plan resolves a closure containing the entire arch-lens pack."""
    from unittest.mock import MagicMock

    from autoskillit.core import SkillResult, ValidatedAddDir
    from autoskillit.workspace.session_skills import (
        DefaultSessionSkillManager,
        SkillsDirectoryProvider,
    )

    real_provider = SkillsDirectoryProvider()
    real_mgr = DefaultSessionSkillManager(provider=real_provider, ephemeral_root=tool_ctx.temp_dir)

    captured: dict = {}

    class _RecordingManager:
        def __init__(self, real: DefaultSessionSkillManager) -> None:
            self._real = real

        def init_session(self, session_id, **kwargs):
            captured["allow_only"] = kwargs.get("allow_only")
            return ValidatedAddDir(path="/fake/session/dir")

        def compute_skill_closure(self, target_name):
            return self._real.compute_skill_closure(target_name)

        def activate_skill_deps(self, session_id, skill_name):
            return True

        def cleanup_stale(self, max_age_seconds=86400):
            return 0

    tool_ctx.session_skill_manager = _RecordingManager(real_mgr)

    mock_resolver = MagicMock()
    mock_resolver.resolve.return_value = MagicMock(source=MagicMock(value="bundled_extended"))
    tool_ctx.skill_resolver = mock_resolver

    class MockExecutor:
        async def run(self, skill_command, cwd, *, add_dirs=(), **kwargs) -> SkillResult:
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

    await run_skill("/autoskillit:make-plan refactor", "/tmp")

    closure = captured["allow_only"]
    assert closure is not None
    assert "make-plan" in closure
    assert "mermaid" in closure
    arch_members = {n for n in closure if n.startswith("arch-lens-")}
    assert len(arch_members) >= 1


def _make_capturing_executor():
    """Return (executor, captured_dict) for testing idle_output_timeout propagation."""
    from autoskillit.core import SkillResult

    captured: dict = {}

    class MockExecutor:
        async def run(
            self, skill_command, cwd, *, idle_output_timeout=None, **kwargs
        ) -> SkillResult:
            captured["idle_output_timeout"] = idle_output_timeout
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

    return MockExecutor(), captured


@pytest.mark.anyio
async def test_run_skill_passes_idle_output_timeout(tool_ctx, monkeypatch) -> None:
    """run_skill passes idle_output_timeout (as float) to executor.run()."""
    executor, captured = _make_capturing_executor()
    tool_ctx.executor = executor
    monkeypatch.setattr("autoskillit.server._ctx", tool_ctx)

    from autoskillit.server.tools_execution import run_skill

    await run_skill("/test skill", "/tmp", idle_output_timeout=120)
    assert captured["idle_output_timeout"] == 120.0  # int→float conversion


@pytest.mark.anyio
async def test_run_skill_idle_output_timeout_defaults_to_none(tool_ctx, monkeypatch) -> None:
    """run_skill passes None to executor.run() when idle_output_timeout is not set."""
    executor, captured = _make_capturing_executor()
    tool_ctx.executor = executor
    monkeypatch.setattr("autoskillit.server._ctx", tool_ctx)

    from autoskillit.server.tools_execution import run_skill

    await run_skill("/test skill", "/tmp")
    assert captured["idle_output_timeout"] is None


@pytest.mark.anyio
async def test_run_skill_returns_structured_error_when_executor_raises(
    tool_ctx, monkeypatch
) -> None:
    """run_skill returns SkillResult-shaped JSON even if executor.run() raises unexpectedly."""
    from autoskillit.core import SkillResult

    class ExplodingExecutor:
        async def run(self, *args, **kwargs) -> SkillResult:
            raise RuntimeError("unexpected executor failure")

    tool_ctx.executor = ExplodingExecutor()
    monkeypatch.setattr("autoskillit.server._ctx", tool_ctx)

    from autoskillit.server.tools_execution import run_skill

    result_json = await run_skill("/test cmd", "/tmp")
    data = json.loads(result_json)
    assert data["success"] is False
    assert data["subtype"] == "crashed"
    assert data["needs_retry"] is False
    assert "unexpected executor failure" in data["result"]
