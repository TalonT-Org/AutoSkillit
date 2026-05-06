"""Tests for run_cmd and run_python MCP tool handlers."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest
import structlog.testing

from autoskillit.server._subprocess import _run_subprocess
from autoskillit.server.tools.tools_execution import run_cmd, run_python
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
        await run_cmd(cmd="echo timeout_test", cwd="/tmp", timeout=30)

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
    """_process_runner_result shared helper lives in server._subprocess."""

    def test_normal_exit_preserves_fields(self):
        from autoskillit.core import TerminationReason
        from autoskillit.execution.process import SubprocessResult
        from autoskillit.server._subprocess import _process_runner_result

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
        from autoskillit.server._subprocess import _process_runner_result

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


class TestRunCmdSleepInterception:
    """Sleep commands are intercepted and converted to asyncio.sleep."""

    @pytest.mark.anyio
    async def test_python_sleep_intercepted(self, tool_ctx):
        result = json.loads(
            await run_cmd(cmd='python3 -c "import time; time.sleep(0)"', cwd="/tmp")
        )
        assert result == {"success": True, "exit_code": 0, "stdout": "", "stderr": ""}
        assert len(tool_ctx.runner.call_args_list) == 0

    @pytest.mark.anyio
    async def test_bare_sleep_intercepted(self, tool_ctx):
        result = json.loads(await run_cmd(cmd="sleep 0", cwd="/tmp"))
        assert result["success"] is True
        assert len(tool_ctx.runner.call_args_list) == 0

    @pytest.mark.anyio
    async def test_python3_single_quotes_intercepted(self, tool_ctx):
        result = json.loads(
            await run_cmd(cmd="python3 -c 'import time; time.sleep(0)'", cwd="/tmp")
        )
        assert result["success"] is True
        assert len(tool_ctx.runner.call_args_list) == 0

    @pytest.mark.anyio
    async def test_non_sleep_uses_subprocess(self, tool_ctx):
        tool_ctx.runner.push(_make_result(0, "hello", ""))
        await run_cmd(cmd="echo hello", cwd="/tmp")
        assert len(tool_ctx.runner.call_args_list) == 1
        assert tool_ctx.runner.call_args_list[0][0] == ["bash", "-c", "echo hello"]

    @pytest.mark.anyio
    async def test_decimal_seconds_intercepted(self, tool_ctx):
        result = json.loads(
            await run_cmd(cmd='python3 -c "import time; time.sleep(0.0)"', cwd="/tmp")
        )
        assert result["success"] is True
        assert len(tool_ctx.runner.call_args_list) == 0

    @pytest.mark.anyio
    async def test_compound_sleep_not_intercepted(self, tool_ctx):
        tool_ctx.runner.push(_make_result(0, "", ""))
        await run_cmd(cmd="echo before && sleep 10 && echo after", cwd="/tmp")
        assert len(tool_ctx.runner.call_args_list) == 1

    @pytest.mark.anyio
    async def test_step_name_timing_recorded(self, tool_ctx):
        await run_cmd(cmd="sleep 0", cwd="/tmp", step_name="quota_wait")
        assert len(tool_ctx.runner.call_args_list) == 0
        assert any(e["step_name"] == "quota_wait" for e in tool_ctx.timing_log.get_report())


# ─── Type coercion tests (Step 1a) ───────────────────────────────────────────


@pytest.mark.usefixtures("tool_ctx")
class TestImportAndCallTypeCoercion:
    """Test _import_and_call annotation-aware type coercion."""

    @pytest.mark.anyio
    async def test_int_coerced_to_str(self):
        """int value for str-annotated param is coerced to str."""
        result = json.loads(
            await run_python(
                callable="tests.server._type_coercion_fixtures._str_only_param",
                args={"value": 42},
                timeout=10,
            )
        )
        assert result["success"] is True
        assert result["result"]["value"] == "42"

    @pytest.mark.anyio
    async def test_str_coerced_to_int(self):
        """str value for int-annotated param is coerced to int."""
        result = json.loads(
            await run_python(
                callable="tests.server._type_coercion_fixtures._int_param",
                args={"value": "7"},
                timeout=10,
            )
        )
        assert result["success"] is True
        assert result["result"]["value"] == 7

    @pytest.mark.anyio
    async def test_str_coerced_to_float(self):
        """str value for float-annotated param is coerced to float."""
        result = json.loads(
            await run_python(
                callable="tests.server._type_coercion_fixtures._typed_callable",
                args={"name": "test", "count": 1, "ratio": "3.14"},
                timeout=10,
            )
        )
        assert result["success"] is True
        assert result["result"]["ratio"] == 3.14

    @pytest.mark.anyio
    async def test_int_coerced_to_float(self):
        """int value for float-annotated param is coerced to float."""
        result = json.loads(
            await run_python(
                callable="tests.server._type_coercion_fixtures._typed_callable",
                args={"name": "test", "count": 1, "ratio": 5},
                timeout=10,
            )
        )
        assert result["success"] is True
        assert result["result"]["ratio"] == 5.0

    @pytest.mark.anyio
    async def test_correct_types_unchanged(self):
        """Correct types pass through without coercion."""
        result = json.loads(
            await run_python(
                callable="tests.server._type_coercion_fixtures._typed_callable",
                args={"name": "hello", "count": 7, "ratio": 2.5},
                timeout=10,
            )
        )
        assert result["success"] is True
        assert result["result"] == {"name": "hello", "count": 7, "ratio": 2.5}

    @pytest.mark.anyio
    async def test_none_still_uses_default(self):
        """None still triggers default substitution (existing behavior)."""
        result = json.loads(
            await run_python(
                callable="tests.server._type_coercion_fixtures._str_optional_param",
                args={"value": None},
                timeout=10,
            )
        )
        assert result["success"] is True
        assert result["result"]["value"] == "default"

    @pytest.mark.anyio
    async def test_coercion_logs_warning(self):
        """Coercion emits a structlog warning with type info."""
        with structlog.testing.capture_logs() as logs:
            result = json.loads(
                await run_python(
                    callable="tests.server._type_coercion_fixtures._str_only_param",
                    args={"value": 99},
                    timeout=10,
                )
            )
        assert result["success"] is True
        coercion_logs = [log for log in logs if log.get("event") == "run_python type coerced"]
        assert len(coercion_logs) == 1
        assert coercion_logs[0]["arg"] == "value"
        assert coercion_logs[0]["from_type"] == "int"
        assert coercion_logs[0]["to_type"] == "str"

    @pytest.mark.anyio
    async def test_unconvertible_value_not_coerced(self):
        """Non-numeric str for int param is not coerced, callable fails."""
        result = json.loads(
            await run_python(
                callable="tests.server._type_coercion_fixtures._int_param",
                args={"value": "not_a_number"},
                timeout=10,
            )
        )
        assert result["success"] is False
        assert "AssertionError" in result["error"]

    @pytest.mark.anyio
    async def test_sentinel_keys_stripped_from_args(self):
        """run_python sentinel keys in args dict must be stripped before callable dispatch."""
        result = json.loads(
            await run_python(
                callable="tests.server._type_coercion_fixtures._str_only_param",
                args={"value": "x", "timeout": 60, "callable": "bogus.path"},
                timeout=10,
            )
        )
        assert result["success"] is True
        assert result["result"]["value"] == "x"

    @pytest.mark.anyio
    async def test_unrecognized_args_dropped_with_warning(self):
        """_import_and_call should log a warning and drop args not in the callable's signature."""
        with structlog.testing.capture_logs() as logs:
            result = json.loads(
                await run_python(
                    callable="tests.server._type_coercion_fixtures._str_only_param",
                    args={"value": "x", "bogus": 42},
                    timeout=10,
                )
            )
        assert result["success"] is True
        assert result["result"]["value"] == "x"
        warning_logs = [log for log in logs if "bogus" in str(log.get("extra_args", []))]
        assert len(warning_logs) >= 1

    @pytest.mark.anyio
    async def test_extra_args_forwarded_when_callable_accepts_kwargs(self):
        """When the callable accepts **kwargs, extra args should be forwarded."""
        result = json.loads(
            await run_python(
                callable="tests.server._type_coercion_fixtures._kwargs_callable",
                args={"name": "test", "extra": "y"},
                timeout=10,
            )
        )
        assert result["success"] is True
        assert result["result"]["name"] == "test"
        assert result["result"]["extras"]["extra"] == "y"
