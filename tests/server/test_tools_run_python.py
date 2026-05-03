"""Unit tests for run_python: observability and headless gate enforcement."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock

import pytest
import structlog.contextvars
import structlog.testing
from autoskillit.server.tools_execution import run_python

pytestmark = [pytest.mark.layer("server"), pytest.mark.small]


class TestRunPythonObservability:
    """run_python binds structlog contextvars and calls ctx.info/ctx.error."""

    @pytest.fixture
    def mock_ctx(self):
        ctx = AsyncMock()
        ctx.info = AsyncMock()
        ctx.error = AsyncMock()
        return ctx

    @pytest.mark.anyio
    async def test_run_python_binds_tool_contextvar_and_calls_ctx_info(self, tool_ctx, mock_ctx):
        """run_python binds tool='run_python' contextvar and calls ctx.info on success."""
        with structlog.testing.capture_logs(
            processors=[structlog.contextvars.merge_contextvars]
        ) as logs:
            await run_python(callable="json.dumps", args={"obj": 1}, ctx=mock_ctx)
        assert any(entry.get("tool") == "run_python" for entry in logs)

    @pytest.mark.anyio
    async def test_run_python_returns_failure_result_on_bad_module(self, tool_ctx, mock_ctx):
        """run_python reports failure (success=false) when callable import fails."""
        result = json.loads(await run_python(callable="nonexistent.module.func", ctx=mock_ctx))
        assert result["success"] is False


class TestRunPythonHeadlessGate:
    """run_python returns headless_error when AUTOSKILLIT_HEADLESS=1."""

    @pytest.fixture(autouse=True)
    def _set_headless_env(self, monkeypatch):
        monkeypatch.setenv("AUTOSKILLIT_HEADLESS", "1")

    @pytest.mark.anyio
    async def test_run_python_blocked_in_headless_session(self, tool_ctx):
        """run_python returns headless_error when AUTOSKILLIT_HEADLESS=1."""
        result = json.loads(await run_python("os.getcwd"))
        assert result["subtype"] == "headless_error"
