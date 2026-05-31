"""Tests for server-side pipeline dependency enforcement in run_skill."""

from __future__ import annotations

import json

import pytest

from autoskillit.server.tools.tools_execution import run_skill

pytestmark = [pytest.mark.layer("server"), pytest.mark.small]


def _write_tracker(tmp_path, pipeline_id, steps, dependencies):
    tracker_dir = tmp_path / ".autoskillit" / "temp" / "pipeline_tracker"
    tracker_dir.mkdir(parents=True, exist_ok=True)
    tracker_dir.joinpath(f"{pipeline_id}.json").write_text(
        json.dumps(
            {
                "pipeline_id": pipeline_id,
                "kitchen_id": "test-kitchen",
                "initialized_at": "2026-05-31T01:00:00Z",
                "steps": steps,
                "dependencies": dependencies,
            }
        )
    )


def _setup_project(tmp_path, tool_ctx_kitchen_open):
    temp_dir = tmp_path / ".autoskillit" / "temp"
    temp_dir.mkdir(parents=True, exist_ok=True)
    (temp_dir / ".hook_config.json").write_text("{}")
    tool_ctx_kitchen_open.project_dir = tmp_path
    tool_ctx_kitchen_open.active_recipe_steps = {"a": {}, "b": {}, "implement": {}}


class TestPipelineDepsDeniesUnmet:
    @pytest.mark.anyio
    async def test_denies_unmet_dependency(self, tool_ctx_kitchen_open, tmp_path):
        _setup_project(tmp_path, tool_ctx_kitchen_open)
        _write_tracker(
            tmp_path,
            "AB",
            {"a": {"status": "pending"}, "b": {"status": "pending"}},
            {"b": ["a"]},
        )
        result = json.loads(
            await run_skill("/do-b task", str(tmp_path), step_name="b", order_id="AB")
        )
        assert result["success"] is False
        assert "DEPENDENCY UNMET" in result["error"]


class TestPipelineDepsAllowsMet:
    @pytest.mark.anyio
    async def test_allows_met_dependency(self, tool_ctx_kitchen_open, tmp_path):
        _setup_project(tmp_path, tool_ctx_kitchen_open)
        _write_tracker(
            tmp_path,
            "AB",
            {
                "a": {"status": "complete", "completed_at": "2026-05-31T01:05:00Z"},
                "b": {"status": "pending"},
            },
            {"b": ["a"]},
        )
        result = json.loads(
            await run_skill("/do-b task", str(tmp_path), step_name="b", order_id="AB")
        )
        assert "DEPENDENCY UNMET" not in result.get("error", "")


class TestPipelineDepsFailOpen:
    @pytest.mark.anyio
    async def test_allows_no_tracker(self, tool_ctx_kitchen_open, tmp_path):
        _setup_project(tmp_path, tool_ctx_kitchen_open)
        result = json.loads(
            await run_skill("/do-b task", str(tmp_path), step_name="b", order_id="ZZ")
        )
        assert "DEPENDENCY UNMET" not in result.get("error", "")

    @pytest.mark.anyio
    async def test_allows_step_not_in_tracker(self, tool_ctx_kitchen_open, tmp_path):
        _setup_project(tmp_path, tool_ctx_kitchen_open)
        _write_tracker(tmp_path, "AB", {"a": {"status": "pending"}}, {})
        result = json.loads(
            await run_skill("/do-unknown task", str(tmp_path), step_name="unknown", order_id="AB")
        )
        assert "DEPENDENCY UNMET" not in result.get("error", "")

    @pytest.mark.anyio
    async def test_allows_step_with_no_dependencies(self, tool_ctx_kitchen_open, tmp_path):
        _setup_project(tmp_path, tool_ctx_kitchen_open)
        _write_tracker(
            tmp_path,
            "AB",
            {"a": {"status": "pending"}, "b": {"status": "pending"}},
            {"b": ["a"]},
        )
        result = json.loads(
            await run_skill("/do-a task", str(tmp_path), step_name="a", order_id="AB")
        )
        assert "DEPENDENCY UNMET" not in result.get("error", "")


class TestPipelineDepsResumeBypass:
    @pytest.mark.anyio
    async def test_allows_resume_of_blocked_step(self, tool_ctx_kitchen_open, tmp_path):
        _setup_project(tmp_path, tool_ctx_kitchen_open)
        _write_tracker(
            tmp_path,
            "AB",
            {"a": {"status": "pending"}, "b": {"status": "pending"}},
            {"b": ["a"]},
        )
        result = json.loads(
            await run_skill(
                "/do-b task",
                str(tmp_path),
                step_name="b",
                order_id="AB",
                resume_session_id="existing-session-123",
            )
        )
        assert "DEPENDENCY UNMET" not in result.get("error", "")


class TestPipelineDepsScoping:
    @pytest.mark.anyio
    async def test_dep_check_uses_order_id_scoping(self, tool_ctx_kitchen_open, tmp_path):
        _setup_project(tmp_path, tool_ctx_kitchen_open)
        _write_tracker(
            tmp_path,
            "AB",
            {"a": {"status": "pending"}, "b": {"status": "pending"}},
            {"b": ["a"]},
        )
        _write_tracker(
            tmp_path,
            "CD",
            {
                "a": {"status": "complete", "completed_at": "2026-05-31T01:05:00Z"},
                "b": {"status": "pending"},
            },
            {"b": ["a"]},
        )
        result = json.loads(
            await run_skill("/do-b task", str(tmp_path), step_name="b", order_id="CD")
        )
        assert "DEPENDENCY UNMET" not in result.get("error", "")

    @pytest.mark.anyio
    async def test_skipped_dep_counts_as_satisfied(self, tool_ctx_kitchen_open, tmp_path):
        _setup_project(tmp_path, tool_ctx_kitchen_open)
        _write_tracker(
            tmp_path,
            "AB",
            {"a": {"status": "skipped"}, "b": {"status": "pending"}},
            {"b": ["a"]},
        )
        result = json.loads(
            await run_skill("/do-b task", str(tmp_path), step_name="b", order_id="AB")
        )
        assert "DEPENDENCY UNMET" not in result.get("error", "")

    @pytest.mark.anyio
    async def test_dep_check_normalizes_step_name_suffix(self, tool_ctx_kitchen_open, tmp_path):
        _setup_project(tmp_path, tool_ctx_kitchen_open)
        _write_tracker(
            tmp_path,
            "AB",
            {"a": {"status": "pending"}, "implement": {"status": "pending"}},
            {"implement": ["a"]},
        )
        result = json.loads(
            await run_skill(
                "/implement task", str(tmp_path), step_name="implement-30", order_id="AB"
            )
        )
        assert result["success"] is False
        assert "DEPENDENCY UNMET" in result["error"]
