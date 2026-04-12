"""Unit tests for run_cmd: observability, timing, and headless gate enforcement."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock

import pytest
import structlog.contextvars
import structlog.testing

from autoskillit.server.tools_execution import run_cmd
from tests.conftest import _make_result


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
