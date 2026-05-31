"""Tests for record_pipeline_step MCP tool."""

from __future__ import annotations

import json

import pytest

from autoskillit.server.tools.tools_pipeline_tracker import record_pipeline_step

pytestmark = [pytest.mark.layer("server"), pytest.mark.small]


class TestRecordPipelineStepInit:
    @pytest.fixture(autouse=True)
    def _setup(self, tool_ctx_kitchen_open, monkeypatch, tmp_path):
        monkeypatch.chdir(tmp_path)
        tool_ctx_kitchen_open.project_dir = tmp_path
        tool_ctx_kitchen_open.kitchen_id = "test-kitchen"
        tool_ctx_kitchen_open.active_recipe_steps = {
            "review": {},
            "implement": {},
            "verify": {},
        }
        self.ctx = tool_ctx_kitchen_open
        self.tmp_path = tmp_path

    @pytest.mark.anyio
    async def test_init_creates_tracker_file(self):
        result = json.loads(
            await record_pipeline_step(
                pipeline_id="AB",
                op="init",
                dependencies={"implement": ["review"]},
            )
        )
        assert result["success"] is True
        assert result["step_count"] == 3
        assert result["dependency_count"] == 1

        tracker_path = self.tmp_path / ".autoskillit" / "temp" / "pipeline_tracker" / "AB.json"
        assert tracker_path.exists()
        tracker = json.loads(tracker_path.read_text())
        assert tracker["pipeline_id"] == "AB"
        assert tracker["kitchen_id"] == "test-kitchen"
        assert set(tracker["steps"].keys()) == {"review", "implement", "verify"}
        assert tracker["dependencies"] == {"implement": ["review"]}

    @pytest.mark.anyio
    async def test_init_marks_locked_steps_as_skipped(self):
        overlay_path = self.tmp_path / ".autoskillit" / "temp" / ".hook_config_overlay.json"
        overlay_path.parent.mkdir(parents=True, exist_ok=True)
        overlay_path.write_text(json.dumps({"locked_steps": {"AB": {"verify": False}}}))

        result = json.loads(await record_pipeline_step(pipeline_id="AB", op="init"))
        assert result["success"] is True

        tracker_path = self.tmp_path / ".autoskillit" / "temp" / "pipeline_tracker" / "AB.json"
        tracker = json.loads(tracker_path.read_text())
        assert tracker["steps"]["verify"]["status"] == "skipped"
        assert tracker["steps"]["review"]["status"] == "pending"

    @pytest.mark.anyio
    async def test_init_pipeline_id_fallback_to_env(self, monkeypatch):
        monkeypatch.setenv("AUTOSKILLIT_DISPATCH_ID", "XY")
        result = json.loads(await record_pipeline_step(pipeline_id="", op="init"))
        assert result["success"] is True
        assert result["pipeline_id"] == "XY"

        tracker_path = self.tmp_path / ".autoskillit" / "temp" / "pipeline_tracker" / "XY.json"
        assert tracker_path.exists()

    @pytest.mark.anyio
    async def test_init_rejects_duplicate_pipeline_id(self):
        await record_pipeline_step(pipeline_id="AB", op="init")
        result = json.loads(await record_pipeline_step(pipeline_id="AB", op="init"))
        assert result["success"] is False
        assert "already been initialized" in result["error"]

    @pytest.mark.anyio
    async def test_init_empty_pipeline_id_no_env_returns_error(self, monkeypatch):
        monkeypatch.delenv("AUTOSKILLIT_DISPATCH_ID", raising=False)
        result = json.loads(await record_pipeline_step(pipeline_id="", op="init"))
        assert result["success"] is False
        assert "pipeline_id is required" in result["error"]


class TestRecordPipelineStepGateClosed:
    @pytest.mark.anyio
    async def test_init_rejects_without_kitchen_open(self, tool_ctx, monkeypatch, tmp_path):
        monkeypatch.chdir(tmp_path)
        tool_ctx.project_dir = tmp_path
        tool_ctx.active_recipe_steps = {"step_a": {}}
        result = json.loads(await record_pipeline_step(pipeline_id="AB", op="init"))
        assert result["success"] is False


class TestRecordPipelineStepStatus:
    @pytest.fixture(autouse=True)
    def _setup(self, tool_ctx_kitchen_open, monkeypatch, tmp_path):
        monkeypatch.chdir(tmp_path)
        tool_ctx_kitchen_open.project_dir = tmp_path
        tool_ctx_kitchen_open.kitchen_id = "test-kitchen"
        self.ctx = tool_ctx_kitchen_open
        self.tmp_path = tmp_path

    @pytest.mark.anyio
    async def test_status_returns_current_state(self):
        tracker_dir = self.tmp_path / ".autoskillit" / "temp" / "pipeline_tracker"
        tracker_dir.mkdir(parents=True)
        tracker_dir.joinpath("AB.json").write_text(
            json.dumps(
                {
                    "pipeline_id": "AB",
                    "kitchen_id": "test-kitchen",
                    "initialized_at": "2026-05-31T01:00:00Z",
                    "steps": {
                        "review": {"status": "complete", "completed_at": "2026-05-31T01:05:00Z"},
                        "implement": {"status": "pending"},
                        "verify": {"status": "skipped"},
                    },
                    "dependencies": {"implement": ["review"]},
                }
            )
        )

        result = json.loads(await record_pipeline_step(pipeline_id="AB", op="status"))
        assert result["success"] is True
        assert result["complete"] == 1
        assert result["pending"] == 1
        assert result["skipped"] == 1
        assert result["total"] == 3


class TestGetPipelineReportIncludesTrackerGaps:
    @pytest.fixture(autouse=True)
    def _setup(self, tool_ctx_kitchen_open, monkeypatch, tmp_path):
        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv("AUTOSKILLIT_SESSION_TYPE", "fleet")
        tool_ctx_kitchen_open.project_dir = tmp_path
        self.ctx = tool_ctx_kitchen_open
        self.tmp_path = tmp_path

    @pytest.mark.anyio
    async def test_get_pipeline_report_includes_tracker_gaps(self):
        from autoskillit.server.tools.tools_status import get_pipeline_report

        tracker_dir = self.tmp_path / ".autoskillit" / "temp" / "pipeline_tracker"
        tracker_dir.mkdir(parents=True)
        tracker_dir.joinpath("AB.json").write_text(
            json.dumps(
                {
                    "pipeline_id": "AB",
                    "kitchen_id": "test-kitchen",
                    "initialized_at": "2026-05-31T01:00:00Z",
                    "steps": {
                        "review": {"status": "complete"},
                        "implement": {"status": "pending"},
                    },
                    "dependencies": {},
                }
            )
        )

        result = json.loads(await get_pipeline_report())
        assert "step_completion_gaps" in result
        gaps = result["step_completion_gaps"]
        assert any(g["pipeline_id"] == "AB" and g["step"] == "implement" for g in gaps)
