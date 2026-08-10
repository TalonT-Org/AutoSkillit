"""Integration tests for server-side pipeline step completion marking in run_skill."""

from __future__ import annotations

import dataclasses
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastmcp.server.middleware import MiddlewareContext
from fastmcp.tools.base import ToolResult
from fastmcp.tools.function_tool import FunctionTool
from mcp.types import CallToolRequestParams, TextContent

from autoskillit.core.types import RetryReason
from autoskillit.core.types._type_results import SkillResult
from autoskillit.server._run_skill_completion import RunSkillCompletionMiddleware
from autoskillit.server.tools.tools_execution import run_skill
from autoskillit.server.tools.tools_pipeline_tracker import (
    complete_run_skill_result,
    recover_run_skill_result,
)
from tests.server._pipeline_test_helpers import (
    _ack_direct_run_skill_result,
    _write_tracker,
)
from tests.server._pipeline_test_helpers import (
    _setup_project as _shared_setup_project,
)

pytestmark = [pytest.mark.layer("integration"), pytest.mark.medium]

_SUCCESS_RESULT = SkillResult(
    success=True,
    result="done",
    session_id="test-session",
    subtype="natural_exit",
    is_error=False,
    exit_code=0,
    needs_retry=False,
    retry_reason=RetryReason.NONE,
    stderr="",
)

_FAIL_RESULT = dataclasses.replace(
    _SUCCESS_RESULT,
    success=False,
    exit_code=1,
    is_error=True,
    subtype="error",
)


def _setup_project(tmp_path, tool_ctx_kitchen_open):
    _shared_setup_project(tmp_path, tool_ctx_kitchen_open)
    tool_ctx_kitchen_open.input_contract_resolver = None


def _read_tracker(tmp_path, kitchen_id="test-kitchen"):
    tracker_path = tmp_path / ".autoskillit" / "temp" / "pipeline_tracker" / f"{kitchen_id}.json"
    return json.loads(tracker_path.read_text())


class TestServerSideStepCompletionMarking:
    @pytest.mark.anyio
    async def test_run_skill_success_marks_step_complete_server_side(
        self, tool_ctx_kitchen_open, tmp_path, monkeypatch
    ):
        monkeypatch.delenv("AUTOSKILLIT_DISPATCH_ID", raising=False)
        _setup_project(tmp_path, tool_ctx_kitchen_open)
        tool_ctx_kitchen_open.kitchen_id = "test-kitchen"
        _write_tracker(
            tmp_path,
            "test-kitchen",
            {"rectify": {"status": "pending"}, "review_approach": {"status": "pending"}},
            {"review_approach": ["rectify"]},
        )
        tool_ctx_kitchen_open.executor = AsyncMock()
        tool_ctx_kitchen_open.executor.run = AsyncMock(return_value=_SUCCESS_RESULT)

        result = json.loads(
            await run_skill("/autoskillit:rectify task", str(tmp_path), step_name="rectify")
        )
        _ack_direct_run_skill_result(tool_ctx_kitchen_open, result)

        tracker = _read_tracker(tmp_path)
        assert tracker["steps"]["rectify"]["status"] == "complete"

    @pytest.mark.anyio
    async def test_wire_delivery_is_acknowledged_through_completion_handler(
        self, tool_ctx_kitchen_open, tmp_path, monkeypatch
    ):
        monkeypatch.delenv("AUTOSKILLIT_DISPATCH_ID", raising=False)
        _setup_project(tmp_path, tool_ctx_kitchen_open)
        tool_ctx_kitchen_open.kitchen_id = "test-kitchen"
        _write_tracker(
            tmp_path,
            "test-kitchen",
            {"rectify": {"status": "pending"}},
            {},
        )
        tool_ctx_kitchen_open.executor = AsyncMock()
        tool_ctx_kitchen_open.executor.run = AsyncMock(return_value=_SUCCESS_RESULT)

        async def placeholder() -> str:
            return "unused"

        registered = FunctionTool.from_function(placeholder, name="run_skill")
        fake_mcp = SimpleNamespace(get_tool=AsyncMock(return_value=registered))
        context = MiddlewareContext(
            message=CallToolRequestParams(name="run_skill", arguments={}),
            fastmcp_context=SimpleNamespace(session_id="request-session"),  # type: ignore[arg-type]
            method="tools/call",
            type="request",
        )

        async def call_next(_context) -> ToolResult:
            rendered = await run_skill(
                "/autoskillit:rectify task",
                str(tmp_path),
                step_name="rectify",
            )
            return registered.convert_result(rendered)

        delivered = await RunSkillCompletionMiddleware(fake_mcp).on_call_tool(  # type: ignore[arg-type]
            context, call_next
        )
        assert len(delivered.content) == 1
        assert isinstance(delivered.content[0], TextContent)
        receipt_id = json.loads(delivered.content[0].text)["receipt_id"]

        completed = json.loads(
            await complete_run_skill_result(
                receipt_id,
                ctx=SimpleNamespace(session_id="request-session"),  # type: ignore[arg-type]
            )
        )

        assert completed["success"] is True
        assert completed["tracker"]["success"] is True
        assert _read_tracker(tmp_path)["steps"]["rectify"]["status"] == "complete"

    @pytest.mark.anyio
    async def test_lost_delivery_is_recovered_by_replacement_request_session(
        self, tool_ctx_kitchen_open, tmp_path, monkeypatch
    ):
        monkeypatch.delenv("AUTOSKILLIT_DISPATCH_ID", raising=False)
        _setup_project(tmp_path, tool_ctx_kitchen_open)
        tool_ctx_kitchen_open.kitchen_id = "test-kitchen"
        _write_tracker(
            tmp_path,
            "test-kitchen",
            {"rectify": {"status": "pending"}},
            {},
        )
        tool_ctx_kitchen_open.executor = AsyncMock()
        tool_ctx_kitchen_open.executor.run = AsyncMock(return_value=_SUCCESS_RESULT)

        async def placeholder() -> str:
            return "unused"

        registered = FunctionTool.from_function(placeholder, name="run_skill")
        fake_mcp = SimpleNamespace(get_tool=AsyncMock(return_value=registered))
        original_context = MiddlewareContext(
            message=CallToolRequestParams(name="run_skill", arguments={}),
            fastmcp_context=SimpleNamespace(session_id="disconnected"),  # type: ignore[arg-type]
            method="tools/call",
            type="request",
        )

        async def call_next(_context) -> ToolResult:
            rendered = await run_skill(
                "/autoskillit:rectify task",
                str(tmp_path),
                step_name="rectify",
            )
            return registered.convert_result(rendered)

        await RunSkillCompletionMiddleware(fake_mcp).on_call_tool(  # type: ignore[arg-type]
            original_context, call_next
        )
        replacement_context = SimpleNamespace(session_id="replacement")

        recovered = json.loads(
            await recover_run_skill_result(ctx=replacement_context)  # type: ignore[arg-type]
        )
        refused = json.loads(
            await complete_run_skill_result(
                recovered["receipt_id"],
                ctx=SimpleNamespace(session_id="disconnected"),  # type: ignore[arg-type]
            )
        )
        completed = json.loads(
            await complete_run_skill_result(
                recovered["receipt_id"],
                ctx=replacement_context,  # type: ignore[arg-type]
            )
        )

        assert recovered["success"] is True
        assert refused["success"] is False
        assert "another request session" in refused["error"]
        assert completed["success"] is True
        assert completed["tracker"]["success"] is True
        assert _read_tracker(tmp_path)["steps"]["rectify"]["status"] == "complete"


class TestDependentStepAllowedAfterServerSideMarking:
    @pytest.mark.anyio
    async def test_dependent_step_allowed_after_server_side_marking(
        self, tool_ctx_kitchen_open, tmp_path, monkeypatch
    ):
        monkeypatch.delenv("AUTOSKILLIT_DISPATCH_ID", raising=False)
        _setup_project(tmp_path, tool_ctx_kitchen_open)
        tool_ctx_kitchen_open.kitchen_id = "test-kitchen"
        _write_tracker(
            tmp_path,
            "test-kitchen",
            {"rectify": {"status": "pending"}, "review_approach": {"status": "pending"}},
            {"review_approach": ["rectify"]},
        )
        tool_ctx_kitchen_open.executor = AsyncMock()
        tool_ctx_kitchen_open.executor.run = AsyncMock(return_value=_SUCCESS_RESULT)

        first = json.loads(
            await run_skill("/autoskillit:rectify task", str(tmp_path), step_name="rectify")
        )
        _ack_direct_run_skill_result(tool_ctx_kitchen_open, first)

        result = json.loads(
            await run_skill(
                "/autoskillit:review-approach .autoskillit/temp/rectify/plan.md",
                str(tmp_path),
                step_name="review_approach",
            )
        )
        assert result.get("success") is True, f"Expected success but got: {result}"
        assert "DEPENDENCY UNMET" not in result.get("error", "")
        _ack_direct_run_skill_result(tool_ctx_kitchen_open, result)


class TestStaleSecondTrackerDoesNotDisableMarking:
    @pytest.mark.anyio
    async def test_stale_second_tracker_does_not_disable_marking(
        self, tool_ctx_kitchen_open, tmp_path, monkeypatch
    ):
        monkeypatch.delenv("AUTOSKILLIT_DISPATCH_ID", raising=False)
        _setup_project(tmp_path, tool_ctx_kitchen_open)
        tool_ctx_kitchen_open.kitchen_id = "test-kitchen"
        _write_tracker(
            tmp_path,
            "test-kitchen",
            {"rectify": {"status": "pending"}, "review_approach": {"status": "pending"}},
            {"review_approach": ["rectify"]},
            kitchen_id="test-kitchen",
        )
        _write_tracker(
            tmp_path,
            "stale-kitchen",
            {"rectify": {"status": "pending"}, "review_approach": {"status": "pending"}},
            {"review_approach": ["rectify"]},
            kitchen_id="stale-kitchen",
        )
        tool_ctx_kitchen_open.executor = AsyncMock()
        tool_ctx_kitchen_open.executor.run = AsyncMock(return_value=_SUCCESS_RESULT)

        result = json.loads(
            await run_skill("/autoskillit:rectify task", str(tmp_path), step_name="rectify")
        )
        _ack_direct_run_skill_result(tool_ctx_kitchen_open, result)

        tracker = _read_tracker(tmp_path, kitchen_id="test-kitchen")
        assert tracker["steps"]["rectify"]["status"] == "complete"
        stale_tracker = _read_tracker(tmp_path, kitchen_id="stale-kitchen")
        assert stale_tracker["steps"]["rectify"]["status"] == "pending"


class TestRetrySuffixFoldsToCanonicalStep:
    @pytest.mark.anyio
    async def test_retry_suffix_folds_to_canonical_step(
        self, tool_ctx_kitchen_open, tmp_path, monkeypatch
    ):
        monkeypatch.delenv("AUTOSKILLIT_DISPATCH_ID", raising=False)
        _setup_project(tmp_path, tool_ctx_kitchen_open)
        tool_ctx_kitchen_open.kitchen_id = "test-kitchen"
        _write_tracker(
            tmp_path,
            "test-kitchen",
            {"rectify": {"status": "pending"}, "review_approach": {"status": "pending"}},
            {"review_approach": ["rectify"]},
        )
        tool_ctx_kitchen_open.executor = AsyncMock()
        tool_ctx_kitchen_open.executor.run = AsyncMock(return_value=_SUCCESS_RESULT)

        result = json.loads(
            await run_skill("/autoskillit:rectify task", str(tmp_path), step_name="rectify-2")
        )
        _ack_direct_run_skill_result(tool_ctx_kitchen_open, result)

        tracker = _read_tracker(tmp_path)
        assert tracker["steps"]["rectify"]["status"] == "complete"


class TestFailureAndNeedsRetryDoNotMarkComplete:
    @pytest.mark.anyio
    async def test_failure_and_needs_retry_do_not_mark_complete(
        self, tool_ctx_kitchen_open, tmp_path, monkeypatch
    ):
        monkeypatch.delenv("AUTOSKILLIT_DISPATCH_ID", raising=False)
        _setup_project(tmp_path, tool_ctx_kitchen_open)
        tool_ctx_kitchen_open.kitchen_id = "test-kitchen"
        _write_tracker(
            tmp_path,
            "test-kitchen",
            {"rectify": {"status": "pending"}, "review_approach": {"status": "pending"}},
            {"review_approach": ["rectify"]},
        )
        tool_ctx_kitchen_open.executor = AsyncMock()
        tool_ctx_kitchen_open.executor.run = AsyncMock(return_value=_FAIL_RESULT)

        result = json.loads(
            await run_skill("/autoskillit:rectify task", str(tmp_path), step_name="rectify")
        )
        _ack_direct_run_skill_result(tool_ctx_kitchen_open, result)

        tracker = _read_tracker(tmp_path)
        assert tracker["steps"]["rectify"]["status"] == "pending"


class TestEmptyStepNameDoesNotWriteTracker:
    @pytest.mark.anyio
    async def test_empty_step_name_does_not_write_tracker(
        self, tool_ctx_kitchen_open, tmp_path, monkeypatch
    ):
        monkeypatch.delenv("AUTOSKILLIT_DISPATCH_ID", raising=False)
        _setup_project(tmp_path, tool_ctx_kitchen_open)
        tool_ctx_kitchen_open.kitchen_id = "test-kitchen"
        tool_ctx_kitchen_open.active_recipe_steps = {}
        _write_tracker(
            tmp_path,
            "test-kitchen",
            {"rectify": {"status": "pending"}, "review_approach": {"status": "pending"}},
            {"review_approach": ["rectify"]},
        )
        tool_ctx_kitchen_open.executor = AsyncMock()
        tool_ctx_kitchen_open.executor.run = AsyncMock(return_value=_SUCCESS_RESULT)

        before = _read_tracker(tmp_path)
        result = json.loads(
            await run_skill("/autoskillit:rectify task", str(tmp_path), step_name="")
        )
        _ack_direct_run_skill_result(tool_ctx_kitchen_open, result)

        after = _read_tracker(tmp_path)
        assert after["steps"] == before["steps"]


class TestAdvisorySurfacedOnUnmetDependents:
    @pytest.mark.anyio
    async def test_advisory_surfaced_when_dependent_still_unmet(
        self, tool_ctx_kitchen_open, tmp_path, monkeypatch
    ):
        """The 'advisory' key mark_step_complete() populates must reach the caller."""
        monkeypatch.delenv("AUTOSKILLIT_DISPATCH_ID", raising=False)
        _setup_project(tmp_path, tool_ctx_kitchen_open)
        tool_ctx_kitchen_open.kitchen_id = "test-kitchen"
        _write_tracker(
            tmp_path,
            "test-kitchen",
            {
                "rectify": {"status": "pending"},
                "other_dep": {"status": "pending"},
                "review_approach": {"status": "pending"},
            },
            {"review_approach": ["rectify", "other_dep"]},
        )
        tool_ctx_kitchen_open.executor = AsyncMock()
        tool_ctx_kitchen_open.executor.run = AsyncMock(return_value=_SUCCESS_RESULT)

        result = json.loads(
            await run_skill("/autoskillit:rectify task", str(tmp_path), step_name="rectify")
        )

        assert result.get("success") is True, f"Expected success but got: {result}"
        tracker_result = _ack_direct_run_skill_result(tool_ctx_kitchen_open, result)
        assert tracker_result is not None
        assert "advisory" in tracker_result, tracker_result
        assert "review_approach" in tracker_result["advisory"]


class TestResumeWithStepNameMarksCompleteOnSuccess:
    @pytest.mark.anyio
    async def test_resume_with_step_name_marks_complete_on_success(
        self, tool_ctx_kitchen_open, tmp_path, monkeypatch
    ):
        monkeypatch.delenv("AUTOSKILLIT_DISPATCH_ID", raising=False)
        _setup_project(tmp_path, tool_ctx_kitchen_open)
        tool_ctx_kitchen_open.kitchen_id = "test-kitchen"
        _write_tracker(
            tmp_path,
            "test-kitchen",
            {"rectify": {"status": "pending"}, "review_approach": {"status": "pending"}},
            {"review_approach": ["rectify"]},
        )
        tool_ctx_kitchen_open.executor = AsyncMock()
        tool_ctx_kitchen_open.executor.run = AsyncMock(
            return_value=dataclasses.replace(
                _SUCCESS_RESULT,
                session_id="existing-session-123",
            )
        )
        from tests.conftest import bind_test_skill_resume_contract

        bind_test_skill_resume_contract(
            tool_ctx_kitchen_open,
            session_id="existing-session-123",
            cwd=tmp_path,
            skill_name="rectify",
            resolved_command="/rectify",
        )

        result = json.loads(
            await run_skill(
                "continue the work",
                str(tmp_path),
                step_name="rectify",
                resume_session_id="existing-session-123",
            )
        )
        assert result["success"] is True, result["result"]
        _ack_direct_run_skill_result(tool_ctx_kitchen_open, result)

        tracker = _read_tracker(tmp_path)
        assert tracker["steps"]["rectify"]["status"] == "complete"
