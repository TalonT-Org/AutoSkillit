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


class TestPipelineDepsKitchenScopedFallback:
    @pytest.mark.anyio
    async def test_run_skill_denies_out_of_order_step(self, tool_ctx_kitchen_open, tmp_path):
        _setup_project(tmp_path, tool_ctx_kitchen_open)
        tool_ctx_kitchen_open.kitchen_id = "kitchen-1"
        _write_tracker(
            tmp_path,
            "kitchen-1",
            {"rectify": {"status": "pending"}, "review_approach": {"status": "pending"}},
            {"review_approach": ["rectify"]},
        )
        result = json.loads(
            await run_skill(
                "/autoskillit:review-approach .autoskillit/temp/rectify/plan.md",
                str(tmp_path),
                step_name="review_approach",
            )
        )
        assert result["success"] is False
        assert "DEPENDENCY UNMET" in result["error"]

    @pytest.mark.anyio
    async def test_run_skill_allows_in_order_step(self, tool_ctx_kitchen_open, tmp_path):
        _setup_project(tmp_path, tool_ctx_kitchen_open)
        tool_ctx_kitchen_open.kitchen_id = "kitchen-1"
        _write_tracker(
            tmp_path,
            "kitchen-1",
            {
                "rectify": {"status": "complete", "completed_at": "2026-05-31T01:05:00Z"},
                "review_approach": {"status": "pending"},
            },
            {"review_approach": ["rectify"]},
        )
        result = json.loads(
            await run_skill(
                "/autoskillit:review-approach .autoskillit/temp/rectify/plan.md",
                str(tmp_path),
                step_name="review_approach",
            )
        )
        assert "DEPENDENCY UNMET" not in result.get("error", "")

    @pytest.mark.anyio
    async def test_check_pipeline_deps_falls_back_to_kitchen_id(
        self, tool_ctx_kitchen_open, tmp_path
    ):
        from autoskillit.server.tools.tools_execution import _check_pipeline_deps

        tool_ctx_kitchen_open.project_dir = tmp_path
        tool_ctx_kitchen_open.kitchen_id = "kitchen-2"
        _write_tracker(
            tmp_path,
            "kitchen-2",
            {"rectify": {"status": "pending"}, "review_approach": {"status": "pending"}},
            {"review_approach": ["rectify"]},
        )
        result = _check_pipeline_deps("review_approach", "")
        assert result is not None
        parsed = json.loads(result)
        assert parsed["success"] is False
        assert "DEPENDENCY UNMET" in parsed["error"]


class TestPipelineDepsEmptyStepNameBypass:
    @pytest.mark.anyio
    async def test_run_skill_denies_empty_step_name_with_active_deps(
        self, tool_ctx_kitchen_open, tmp_path
    ):
        from types import SimpleNamespace

        _setup_project(tmp_path, tool_ctx_kitchen_open)
        tool_ctx_kitchen_open.kitchen_id = "kitchen-3"
        tool_ctx_kitchen_open.active_recipe_steps = {
            "some_other_step": SimpleNamespace(with_args={"skill_command": "/other-skill"}),
        }
        _write_tracker(
            tmp_path,
            "kitchen-3",
            {"rectify": {"status": "pending"}, "review_approach": {"status": "pending"}},
            {"review_approach": ["rectify"]},
        )
        result = json.loads(
            await run_skill(
                "/autoskillit:review-approach .autoskillit/temp/rectify/plan.md",
                str(tmp_path),
                step_name="",
            )
        )
        assert result["success"] is False
        assert "DEPENDENCY UNMET" in result["error"]

    @pytest.mark.anyio
    async def test_run_skill_denies_ambiguous_step_name_with_active_deps(
        self, tool_ctx_kitchen_open, tmp_path
    ):
        from types import SimpleNamespace

        _setup_project(tmp_path, tool_ctx_kitchen_open)
        tool_ctx_kitchen_open.kitchen_id = "kitchen-4"
        tool_ctx_kitchen_open.active_recipe_steps = {
            "investigate_a": SimpleNamespace(
                with_args={"skill_command": "/autoskillit:investigate a"}
            ),
            "investigate_b": SimpleNamespace(
                with_args={"skill_command": "/autoskillit:investigate b"}
            ),
        }
        _write_tracker(
            tmp_path,
            "kitchen-4",
            {"rectify": {"status": "pending"}, "review_approach": {"status": "pending"}},
            {"review_approach": ["rectify"]},
        )
        result = json.loads(
            await run_skill(
                "/autoskillit:investigate c",
                str(tmp_path),
                step_name="",
            )
        )
        assert result["success"] is False
        assert "DEPENDENCY UNMET" in result["error"]


class TestPipelineDepsRecoveryInstruction:
    @pytest.mark.anyio
    async def test_dependency_deny_carries_recovery_instruction(
        self, tool_ctx_kitchen_open, tmp_path
    ):
        from autoskillit.server.tools.tools_execution import _check_pipeline_deps

        _setup_project(tmp_path, tool_ctx_kitchen_open)
        _write_tracker(
            tmp_path,
            "AB",
            {"a": {"status": "pending"}, "b": {"status": "pending"}},
            {"b": ["a"]},
        )
        raw = _check_pipeline_deps("b", "AB")
        assert raw is not None
        result = json.loads(raw)
        assert "record_pipeline_step" in result["error"]
        assert "op='status'" in result["error"] or 'op="status"' in result["error"]


class TestPreflightDenyEnvelopeShape:
    @pytest.mark.anyio
    async def test_preflight_denials_use_canonical_envelope(self, tool_ctx_kitchen_open, tmp_path):
        from autoskillit.server.tools.tools_execution import _check_pipeline_deps

        _setup_project(tmp_path, tool_ctx_kitchen_open)
        _write_tracker(
            tmp_path,
            "AB",
            {"a": {"status": "pending"}, "b": {"status": "pending"}},
            {"b": ["a"]},
        )
        raw = _check_pipeline_deps("b", "AB")
        assert raw is not None
        result = json.loads(raw)
        assert result["success"] is False
        assert result["is_error"] is True
        assert result["error"]
        assert "stage" in result
