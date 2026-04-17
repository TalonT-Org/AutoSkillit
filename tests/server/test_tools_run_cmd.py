"""Tests for run_cmd and run_python MCP tool handlers."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest
import structlog.testing

from autoskillit.server.helpers import _run_subprocess
from autoskillit.server.tools_execution import run_cmd, run_python
from tests.conftest import _make_result, _make_timeout_result

pytestmark = [pytest.mark.layer("server"), pytest.mark.small]


class TestRunCmd:
    """T1, T2: run_cmd executes commands and returns exit code semantics."""

    @pytest.mark.anyio
    async def test_successful_command(self, tool_ctx):
        tool_ctx.runner.push(_make_result(0, "hello\n", ""))
        result = json.loads(await run_cmd(cmd="echo hello", cwd="/tmp"))

        assert result["success"] is True
        assert result["exit_code"] == 0
        assert "hello" in result["stdout"]
        assert len(tool_ctx.runner.call_args_list) == 1
        assert tool_ctx.runner.call_args_list[0][0] == ["bash", "-c", "echo hello"]

    @pytest.mark.anyio
    async def test_failing_command(self, tool_ctx):
        tool_ctx.runner.push(_make_result(1, "", "error"))
        result = json.loads(await run_cmd(cmd="false", cwd="/tmp"))

        assert result["success"] is False
        assert result["exit_code"] == 1

    @pytest.mark.anyio
    async def test_custom_timeout(self, tool_ctx):
        tool_ctx.runner.push(_make_result(0, "", ""))
        await run_cmd(cmd="sleep 1", cwd="/tmp", timeout=30)

        assert tool_ctx.runner.call_args_list[-1][2] == 30.0


class TestRunSubprocessDelegatesToManaged:
    """Verify _run_subprocess delegates to the runner (ToolContext.runner) correctly."""

    @pytest.mark.anyio
    async def test_normal_completion(self, tool_ctx):
        tool_ctx.runner.push(_make_result(0, "output", ""))
        rc, stdout, stderr = await _run_subprocess(["echo", "hi"], cwd="/tmp", timeout=10)
        assert rc == 0
        assert stdout == "output"
        assert stderr == ""

    @pytest.mark.anyio
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


@pytest.mark.usefixtures("tool_ctx")
class TestRunPython:
    """run_python tool: import, call, timeout, async support."""

    @pytest.mark.anyio
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

    @pytest.mark.anyio
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

    @pytest.mark.anyio
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

    @pytest.mark.anyio
    async def test_timeout(self):
        """run_python returns error on timeout."""
        import asyncio as _aio

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

    @pytest.mark.anyio
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

    @pytest.mark.anyio
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
