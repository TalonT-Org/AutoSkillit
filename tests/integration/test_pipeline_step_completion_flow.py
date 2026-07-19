"""Integration tests for server-side pipeline step completion marking in run_skill."""

from __future__ import annotations

import dataclasses
import json
from unittest.mock import AsyncMock

import pytest

from autoskillit.core.types import RetryReason
from autoskillit.core.types._type_results import SkillResult
from autoskillit.server.tools.tools_execution import run_skill

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


def _write_tracker(tmp_path, pipeline_id, steps, dependencies, kitchen_id="test-kitchen"):
    tracker_dir = tmp_path / ".autoskillit" / "temp" / "pipeline_tracker"
    tracker_dir.mkdir(parents=True, exist_ok=True)
    tracker_dir.joinpath(f"{pipeline_id}.json").write_text(
        json.dumps(
            {
                "pipeline_id": pipeline_id,
                "kitchen_id": kitchen_id,
                "initialized_at": "2026-05-31T01:00:00Z",
                "steps": steps,
                "dependencies": dependencies,
            }
        )
    )


def _setup_project(tmp_path, tool_ctx_kitchen_open):
    from autoskillit.recipe.schema import RecipeStep

    temp_dir = tmp_path / ".autoskillit" / "temp"
    temp_dir.mkdir(parents=True, exist_ok=True)
    (temp_dir / ".hook_config.json").write_text("{}")
    tool_ctx_kitchen_open.project_dir = tmp_path
    tool_ctx_kitchen_open.active_recipe_steps = {
        "rectify": RecipeStep(name="rectify"),
        "review_approach": RecipeStep(name="review_approach"),
    }
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

        await run_skill("/autoskillit:rectify task", str(tmp_path), step_name="rectify")

        tracker = _read_tracker(tmp_path)
        assert tracker["steps"]["rectify"]["status"] == "complete"


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

        await run_skill("/autoskillit:rectify task", str(tmp_path), step_name="rectify")

        result = json.loads(
            await run_skill(
                "/autoskillit:review-approach .autoskillit/temp/rectify/plan.md",
                str(tmp_path),
                step_name="review_approach",
            )
        )
        assert result.get("success") is True, f"Expected success but got: {result}"
        assert "DEPENDENCY UNMET" not in result.get("error", "")


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

        await run_skill("/autoskillit:rectify task", str(tmp_path), step_name="rectify")

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

        await run_skill("/autoskillit:rectify task", str(tmp_path), step_name="rectify-2")

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

        await run_skill("/autoskillit:rectify task", str(tmp_path), step_name="rectify")

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
        await run_skill("/autoskillit:rectify task", str(tmp_path), step_name="")

        after = _read_tracker(tmp_path)
        assert after["steps"] == before["steps"]


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
        tool_ctx_kitchen_open.executor.run = AsyncMock(return_value=_SUCCESS_RESULT)

        await run_skill(
            "continue the work",
            str(tmp_path),
            step_name="rectify",
            resume_session_id="existing-session-123",
        )

        tracker = _read_tracker(tmp_path)
        assert tracker["steps"]["rectify"]["status"] == "complete"
