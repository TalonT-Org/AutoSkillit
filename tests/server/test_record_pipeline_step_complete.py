"""Tests for record_pipeline_step op='complete'."""

from __future__ import annotations

import json

import pytest

from autoskillit.server.tools.tools_pipeline_tracker import record_pipeline_step
from tests.server._pipeline_test_helpers import _write_tracker

pytestmark = [pytest.mark.layer("server"), pytest.mark.small]


def _grant_success_credit(tool_ctx, tmp_path, step_name: str) -> None:
    authority = tool_ctx.run_skill_completion
    assert authority is not None
    tracker_path = tmp_path / ".autoskillit" / "temp" / "pipeline_tracker" / "test-kitchen.json"
    invocation_id = authority.begin(
        kitchen_id="test-kitchen",
        request_session_id="request-session",
        tracker_order_id="test-kitchen",
        tracker_path=str(tracker_path.resolve()),
        tracker_kitchen_id="test-kitchen",
        tracker_incarnation_id="test-incarnation",
        step_name=step_name,
    )
    receipt = authority.draft(
        invocation_id,
        classification="success",
        success=True,
        result_digest="digest",
    )
    authority.publish(receipt.receipt_id)
    authority.acknowledge(
        receipt.receipt_id,
        kitchen_id="test-kitchen",
        request_session_id="request-session",
    )


class TestRecordPipelineStepComplete:
    @pytest.mark.anyio
    async def test_marks_step_complete(self, tool_ctx_kitchen_open, tmp_path):
        tool_ctx_kitchen_open.project_dir = tmp_path
        tool_ctx_kitchen_open.kitchen_id = "test-kitchen"
        _write_tracker(tmp_path, "test-kitchen", {"rectify": {"status": "pending"}}, {})
        _grant_success_credit(tool_ctx_kitchen_open, tmp_path, "rectify")
        result = json.loads(
            await record_pipeline_step(
                pipeline_id="test-kitchen", op="complete", step_name="rectify"
            )
        )
        assert result["success"] is True
        assert result["step"] == "rectify"
        assert result["status"] == "complete"
        # Verify on disk
        tracker = json.loads(
            (
                tmp_path / ".autoskillit" / "temp" / "pipeline_tracker" / "test-kitchen.json"
            ).read_text()
        )
        assert tracker["steps"]["rectify"]["status"] == "complete"
        assert "completed_at" in tracker["steps"]["rectify"]

    @pytest.mark.anyio
    async def test_canonicalizes_suffix(self, tool_ctx_kitchen_open, tmp_path):
        tool_ctx_kitchen_open.project_dir = tmp_path
        tool_ctx_kitchen_open.kitchen_id = "test-kitchen"
        _write_tracker(tmp_path, "test-kitchen", {"rectify": {"status": "pending"}}, {})
        _grant_success_credit(tool_ctx_kitchen_open, tmp_path, "rectify-2")
        result = json.loads(
            await record_pipeline_step(
                pipeline_id="test-kitchen", op="complete", step_name="rectify-2"
            )
        )
        assert result["success"] is True
        assert result["step"] == "rectify"

    @pytest.mark.anyio
    async def test_errors_on_unknown_step(self, tool_ctx_kitchen_open, tmp_path):
        tool_ctx_kitchen_open.project_dir = tmp_path
        tool_ctx_kitchen_open.kitchen_id = "test-kitchen"
        _write_tracker(tmp_path, "test-kitchen", {"rectify": {"status": "pending"}}, {})
        _grant_success_credit(tool_ctx_kitchen_open, tmp_path, "nonexistent")
        result = json.loads(
            await record_pipeline_step(
                pipeline_id="test-kitchen", op="complete", step_name="nonexistent"
            )
        )
        assert result["success"] is False
        assert "nonexistent" in result["error"]

    @pytest.mark.anyio
    async def test_denies_completion_without_acknowledged_success(
        self, tool_ctx_kitchen_open, tmp_path
    ):
        tool_ctx_kitchen_open.project_dir = tmp_path
        tool_ctx_kitchen_open.kitchen_id = "test-kitchen"
        _write_tracker(tmp_path, "test-kitchen", {"rectify": {"status": "pending"}}, {})

        result = json.loads(
            await record_pipeline_step(
                pipeline_id="test-kitchen", op="complete", step_name="rectify"
            )
        )

        assert result["success"] is False
        assert "acknowledged success credit" in result["error"]

    @pytest.mark.anyio
    async def test_errors_on_missing_step_name(self, tool_ctx_kitchen_open, tmp_path):
        tool_ctx_kitchen_open.project_dir = tmp_path
        tool_ctx_kitchen_open.kitchen_id = "test-kitchen"
        _write_tracker(tmp_path, "test-kitchen", {"rectify": {"status": "pending"}}, {})
        result = json.loads(
            await record_pipeline_step(pipeline_id="test-kitchen", op="complete", step_name="")
        )
        assert result["success"] is False
        assert "step_name is required" in result["error"]

    @pytest.mark.anyio
    async def test_errors_on_unresolvable_pipeline(
        self, tool_ctx_kitchen_open, tmp_path, monkeypatch
    ):
        monkeypatch.delenv("AUTOSKILLIT_DISPATCH_ID", raising=False)
        tool_ctx_kitchen_open.project_dir = tmp_path
        tool_ctx_kitchen_open.kitchen_id = ""
        result = json.loads(
            await record_pipeline_step(pipeline_id="", op="complete", step_name="rectify")
        )
        assert result["success"] is False
        assert "cannot resolve pipeline tracker" in result["error"]

    @pytest.mark.anyio
    async def test_malformed_tracker_is_not_retriable(self, tool_ctx_kitchen_open, tmp_path):
        tool_ctx_kitchen_open.project_dir = tmp_path
        tool_ctx_kitchen_open.kitchen_id = "test-kitchen"
        _write_tracker(tmp_path, "test-kitchen", {"rectify": {"status": "pending"}}, {})
        tracker_path = (
            tmp_path / ".autoskillit" / "temp" / "pipeline_tracker" / "test-kitchen.json"
        )
        tracker_path.write_text("{")

        result = json.loads(
            await record_pipeline_step(
                pipeline_id="test-kitchen", op="complete", step_name="rectify"
            )
        )

        assert result["success"] is False
        assert result["retriable"] is False
        assert "failed to read tracker identity" in result["error"]
