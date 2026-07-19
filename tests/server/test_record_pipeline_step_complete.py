"""Tests for record_pipeline_step op='complete'."""

from __future__ import annotations

import json

import pytest

from autoskillit.server.tools.tools_pipeline_tracker import record_pipeline_step

pytestmark = [pytest.mark.layer("server"), pytest.mark.small]


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


class TestRecordPipelineStepComplete:
    @pytest.mark.anyio
    async def test_marks_step_complete(self, tool_ctx_kitchen_open, tmp_path):
        tool_ctx_kitchen_open.project_dir = tmp_path
        tool_ctx_kitchen_open.kitchen_id = "test-kitchen"
        _write_tracker(tmp_path, "test-kitchen", {"rectify": {"status": "pending"}}, {})
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
        result = json.loads(
            await record_pipeline_step(
                pipeline_id="test-kitchen", op="complete", step_name="nonexistent"
            )
        )
        assert result["success"] is False
        assert "nonexistent" in result["error"]

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
