"""Unit tests for run_cmd: observability, timing, and headless gate enforcement."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock

import pytest
import structlog.contextvars
import structlog.testing

from autoskillit.server.tools_execution import run_cmd
from tests.conftest import _make_result

pytestmark = [pytest.mark.layer("server")]


class TestRunCmdObservability:
    """run_cmd binds structlog contextvars and calls ctx.info/ctx.error."""

    @pytest.fixture
    def mock_ctx(self):
        ctx = AsyncMock()
        ctx.info = AsyncMock()
        ctx.error = AsyncMock()
        return ctx

    @pytest.mark.anyio
    async def test_run_cmd_binds_tool_contextvar_and_calls_ctx_info(self, tool_ctx, mock_ctx):
        """run_cmd binds tool='run_cmd' contextvar and calls ctx.info on success."""
        tool_ctx.runner.push(_make_result(0, "ok\n", ""))
        with structlog.testing.capture_logs(
            processors=[structlog.contextvars.merge_contextvars]
        ) as logs:
            await run_cmd(cmd="echo ok", cwd="/tmp", ctx=mock_ctx)
        assert any(entry.get("tool") == "run_cmd" for entry in logs)

    @pytest.mark.anyio
    async def test_run_cmd_returns_failure_result_on_nonzero_exit(self, tool_ctx, mock_ctx):
        """run_cmd reports failure (success=false) when subprocess exits non-zero."""
        tool_ctx.runner.push(_make_result(1, "", "err"))
        result = json.loads(await run_cmd(cmd="false", cwd="/tmp", ctx=mock_ctx))
        assert result["success"] is False
        assert result["exit_code"] == 1


class TestRunCmdTiming:
    """run_cmd accumulates wall-clock timing when step_name is provided."""

    @pytest.mark.anyio
    async def test_run_cmd_step_name_records_timing(self, tool_ctx):
        await run_cmd(cmd="echo hi", cwd="/tmp", step_name="clone")
        report = tool_ctx.timing_log.get_report()
        assert len(report) == 1
        assert report[0]["step_name"] == "clone"
        assert report[0]["total_seconds"] >= 0.0
        assert report[0]["invocation_count"] == 1

    @pytest.mark.anyio
    async def test_run_cmd_empty_step_name_skips_timing(self, tool_ctx):
        await run_cmd(cmd="echo hi", cwd="/tmp")
        assert tool_ctx.timing_log.get_report() == []


class TestRunCmdRecording:
    """run_cmd threads SCENARIO_STEP_NAME into env kwarg for RecordingSubprocessRunner."""

    @pytest.mark.anyio
    async def test_run_cmd_with_step_name_passes_scenario_step_name_to_runner(self, tool_ctx):
        """run_cmd with step_name passes SCENARIO_STEP_NAME in env kwarg to ctx.runner."""
        from autoskillit.execution.recording import SCENARIO_STEP_NAME_ENV

        tool_ctx.runner.push(_make_result(0, "ok", ""))
        await run_cmd(cmd="echo hi", cwd="/tmp", step_name="setup")
        call_kwargs = tool_ctx.runner.call_args_list[-1][3]
        assert call_kwargs.get("env", {}).get(SCENARIO_STEP_NAME_ENV) == "setup"

    @pytest.mark.anyio
    async def test_run_cmd_with_step_name_preserves_parent_env(self, tool_ctx, monkeypatch):
        """run_cmd with step_name must not strip PATH/HOME from the child env."""
        from autoskillit.execution.recording import SCENARIO_STEP_NAME_ENV

        monkeypatch.setenv("PATH", "/usr/bin:/usr/local/bin")
        monkeypatch.setenv("HOME", "/home/testuser")
        tool_ctx.runner.push(_make_result(0, "ok", ""))
        await run_cmd(cmd="echo hi", cwd="/tmp", step_name="setup")
        call_kwargs = tool_ctx.runner.call_args_list[-1][3]
        env = call_kwargs["env"]
        assert env is not None
        assert "PATH" in env, "run_cmd stripped PATH from child environment"
        assert "HOME" in env, "run_cmd stripped HOME from child environment"
        assert env[SCENARIO_STEP_NAME_ENV] == "setup"

    @pytest.mark.anyio
    async def test_run_cmd_without_step_name_passes_no_env(self, tool_ctx):
        """run_cmd without step_name passes env=None (no SCENARIO_STEP_NAME in env)."""
        from autoskillit.execution.recording import SCENARIO_STEP_NAME_ENV

        tool_ctx.runner.push(_make_result(0, "", ""))
        await run_cmd(cmd="echo hi", cwd="/tmp")
        call_kwargs = tool_ctx.runner.call_args_list[-1][3]
        env = call_kwargs.get("env")
        assert env is None or SCENARIO_STEP_NAME_ENV not in env

    @pytest.mark.anyio
    async def test_run_cmd_with_step_name_records_non_session_step(self, tool_ctx, monkeypatch):
        """End-to-end: run_cmd step_name → RecordingSubprocessRunner.record_non_session_step()."""
        from unittest.mock import Mock

        from autoskillit.execution.recording import RecordingSubprocessRunner
        from tests.conftest import _make_result as _mr
        from tests.fakes import MockSubprocessRunner

        mock_recorder = Mock()
        inner = MockSubprocessRunner()
        inner.push(_mr(0, "task output", ""))
        recording_runner = RecordingSubprocessRunner(recorder=mock_recorder, inner=inner)
        tool_ctx.runner = recording_runner

        await run_cmd(cmd="task install-worktree", cwd="/tmp", step_name="setup")

        mock_recorder.record_non_session_step.assert_called_once_with(
            step_name="setup",
            tool="run_cmd",
            result_summary={"exit_code": 0, "stdout_head": "task output"},
        )

    @pytest.mark.anyio
    async def test_run_cmd_without_step_name_skips_recording(self, tool_ctx, monkeypatch):
        """run_cmd without step_name does not call record_non_session_step."""
        from unittest.mock import Mock

        from autoskillit.execution.recording import RecordingSubprocessRunner
        from tests.conftest import _make_result as _mr
        from tests.fakes import MockSubprocessRunner

        mock_recorder = Mock()
        inner = MockSubprocessRunner()
        inner.push(_mr(0, "", ""))
        recording_runner = RecordingSubprocessRunner(recorder=mock_recorder, inner=inner)
        tool_ctx.runner = recording_runner

        await run_cmd(cmd="echo hi", cwd="/tmp")

        mock_recorder.record_non_session_step.assert_not_called()


class TestRunCmdHeadlessGate:
    """run_cmd returns headless_error when AUTOSKILLIT_HEADLESS=1."""

    @pytest.fixture(autouse=True)
    def _set_headless_env(self, monkeypatch):
        monkeypatch.setenv("AUTOSKILLIT_HEADLESS", "1")

    @pytest.mark.anyio
    async def test_run_cmd_blocked_in_headless_session(self, tool_ctx):
        """run_cmd returns headless_error when AUTOSKILLIT_HEADLESS=1."""
        result = json.loads(await run_cmd("echo hello", "/tmp"))
        assert result["subtype"] == "headless_error"
