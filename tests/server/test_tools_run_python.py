"""Unit tests for run_python: observability and headless gate enforcement."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock

import pytest
import structlog.contextvars
import structlog.testing

import autoskillit.server.tools.tools_execution as tools_execution
from autoskillit.server.tools.tools_execution import run_python
from tests.server._recipe_segment_test_helpers import install_prepared_recipe_segment

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
    async def test_run_python_binds_tool_contextvar_and_calls_ctx_info(
        self, tool_ctx_kitchen_open, mock_ctx
    ):
        """run_python binds tool='run_python' contextvar and calls ctx.info on success."""
        with structlog.testing.capture_logs(
            processors=[structlog.contextvars.merge_contextvars]
        ) as logs:
            await run_python(callable="json.dumps", args={"obj": 1}, ctx=mock_ctx)
        assert logs, "Expected at least one log record"
        assert all(entry.get("tool") == "run_python" for entry in logs)

    @pytest.mark.anyio
    async def test_run_python_returns_failure_result_on_bad_module(self, tool_ctx, mock_ctx):
        """run_python reports failure (success=false) when callable import fails."""
        result = json.loads(await run_python(callable="nonexistent.module.func", ctx=mock_ctx))
        assert result["success"] is False

    @pytest.mark.parametrize(
        ("callable_name", "args", "expected_kind"),
        [
            ("json.dumps", {"obj": 1}, "success"),
            ("nonexistent.module.func", {}, "recovery"),
        ],
    )
    @pytest.mark.anyio
    async def test_run_python_selects_carrier_from_result_success(
        self,
        tool_ctx_kitchen_open,
        mock_ctx,
        monkeypatch: pytest.MonkeyPatch,
        callable_name: str,
        args: dict[str, object],
        expected_kind: str,
    ) -> None:
        install_prepared_recipe_segment(monkeypatch, tools_execution, step_name="python")

        result = json.loads(
            await run_python(
                callable=callable_name,
                args=args,
                step_name="python",
                ctx=mock_ctx,
            )
        )

        assert result["success"] is (expected_kind == "success")
        assert result["recipe_segment"]["kind"] == expected_kind

    @pytest.mark.anyio
    async def test_run_python_selects_carrier_from_shaped_failure(
        self,
        tool_ctx_kitchen_open,
        mock_ctx,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        install_prepared_recipe_segment(monkeypatch, tools_execution, step_name="python")
        monkeypatch.setattr(
            tools_execution,
            "shape_execution_response",
            lambda *_args, **_kwargs: json.dumps(
                {"success": False, "error": "response shaping failed"}
            ),
        )

        result = json.loads(
            await run_python(
                callable="json.dumps",
                args={"obj": 1},
                step_name="python",
                ctx=mock_ctx,
            )
        )

        assert result["success"] is False
        assert result["recipe_segment"]["kind"] == "recovery"


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
