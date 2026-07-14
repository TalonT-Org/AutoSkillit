"""Tests for the review_approach plan-path input contract in run_skill."""

from __future__ import annotations

import json

import pytest

from autoskillit.server.tools.tools_execution import run_skill

pytestmark = [pytest.mark.layer("server"), pytest.mark.small]


def _setup_project(tmp_path, tool_ctx_kitchen_open):
    temp_dir = tmp_path / ".autoskillit" / "temp"
    temp_dir.mkdir(parents=True, exist_ok=True)
    (temp_dir / ".hook_config.json").write_text("{}")
    tool_ctx_kitchen_open.project_dir = tmp_path


class TestReviewApproachRequiresPlanPath:
    @pytest.mark.anyio
    async def test_run_skill_review_approach_requires_plan_path(
        self, tool_ctx_kitchen_open, tmp_path
    ):
        _setup_project(tmp_path, tool_ctx_kitchen_open)
        result = json.loads(
            await run_skill(
                "/autoskillit:review-approach https://github.com/org/repo/issues/123",
                str(tmp_path),
                step_name="review_approach",
            )
        )
        assert result["success"] is False
        assert "plan file path" in result["error"]

    @pytest.mark.anyio
    async def test_run_skill_review_approach_allows_plan_path(
        self, tool_ctx_kitchen_open, tmp_path
    ):
        _setup_project(tmp_path, tool_ctx_kitchen_open)
        result = json.loads(
            await run_skill(
                "/autoskillit:review-approach .autoskillit/temp/rectify/plan.md",
                str(tmp_path),
                step_name="review_approach",
            )
        )
        assert "plan file path" not in result.get("error", "")

    @pytest.mark.anyio
    async def test_run_skill_other_step_allows_url_argument(self, tool_ctx_kitchen_open, tmp_path):
        _setup_project(tmp_path, tool_ctx_kitchen_open)
        result = json.loads(
            await run_skill(
                "/autoskillit:investigate https://github.com/org/repo/issues/123",
                str(tmp_path),
                step_name="investigate",
            )
        )
        assert "plan file path" not in result.get("error", "")
