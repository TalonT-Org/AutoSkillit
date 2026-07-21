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


def _configure_open_kitchen_mock(ctx, steps, tmp_path):
    from unittest.mock import MagicMock

    ctx.project_dir = tmp_path
    ctx.temp_dir = tmp_path / ".autoskillit" / "temp"
    ctx.recipes.load_and_validate.return_value = {
        "content": "name: remediation\nsteps: {}\n",
        "valid": True,
        "errors": [],
        "requires_packs": [],
        "requires_features": [],
        "content_hash": "abc",
        "composite_hash": "def",
        "recipe_version": "1.0",
        "suggestions": [],
        "post_prune_step_names": list(steps.keys()),
    }
    mock_recipe_info = MagicMock()
    mock_recipe_info.path = tmp_path / "remediation.yaml"
    ctx.recipes.find.return_value = mock_recipe_info
    mock_recipe_obj = MagicMock()
    mock_recipe_obj.steps = steps
    mock_recipe_obj.ingredients = {}
    ctx.recipes.load.return_value = mock_recipe_obj


class TestOpenKitchenAutoInitTracker:
    @pytest.mark.anyio
    async def test_open_kitchen_auto_inits_tracker(self, tmp_path):
        from unittest.mock import patch

        from autoskillit.recipe.schema import RecipeStep
        from autoskillit.server.tools.tools_kitchen import open_kitchen
        from tests.server.conftest import _make_mock_ctx

        temp_dir = tmp_path / ".autoskillit" / "temp"
        temp_dir.mkdir(parents=True)
        (temp_dir / ".hook_config.json").write_text("{}")

        steps = {
            "rectify": RecipeStep(name="rectify", on_success="review_approach"),
            "review_approach": RecipeStep(name="review_approach", on_success="dry_walkthrough"),
        }

        ctx = _make_mock_ctx()
        ctx.gate.enabled = True
        ctx.gate_infrastructure_ready = True
        ctx.recipe_name = ""
        ctx.kitchen_id = "kitchen-abc"
        _configure_open_kitchen_mock(ctx, steps, tmp_path)

        with patch("autoskillit.server._get_ctx", return_value=ctx):
            result = json.loads(await open_kitchen(name="remediation", ctx=ctx))

        assert result["success"] is True
        tracker_path = tmp_path / ".autoskillit" / "temp" / "pipeline_tracker" / "kitchen-abc.json"
        assert tracker_path.exists()
        tracker = json.loads(tracker_path.read_text())
        assert tracker["dependencies"].get("review_approach") == ["rectify"]

    @pytest.mark.anyio
    async def test_open_kitchen_auto_init_idempotent(self, tmp_path):
        from unittest.mock import patch

        from autoskillit.recipe.schema import RecipeStep
        from autoskillit.server.tools.tools_kitchen import open_kitchen
        from tests.server.conftest import _make_mock_ctx

        temp_dir = tmp_path / ".autoskillit" / "temp"
        temp_dir.mkdir(parents=True)
        (temp_dir / ".hook_config.json").write_text("{}")

        steps = {
            "rectify": RecipeStep(name="rectify", on_success="review_approach"),
            "review_approach": RecipeStep(name="review_approach", on_success="dry_walkthrough"),
        }

        ctx1 = _make_mock_ctx()
        ctx1.gate.enabled = True
        ctx1.gate_infrastructure_ready = True
        ctx1.recipe_name = ""
        ctx1.kitchen_id = "kitchen-abc"
        _configure_open_kitchen_mock(ctx1, steps, tmp_path)
        with patch("autoskillit.server._get_ctx", return_value=ctx1):
            result1 = json.loads(await open_kitchen(name="remediation", ctx=ctx1))
        assert result1["success"] is True

        tracker_path = tmp_path / ".autoskillit" / "temp" / "pipeline_tracker" / "kitchen-abc.json"
        assert tracker_path.exists()
        tracker = json.loads(tracker_path.read_text())
        tracker["steps"]["rectify"]["status"] = "complete"
        tracker_path.write_text(json.dumps(tracker))

        ctx2 = _make_mock_ctx()
        ctx2.gate.enabled = True
        ctx2.gate_infrastructure_ready = True
        ctx2.recipe_name = "remediation"
        ctx2.kitchen_id = "kitchen-abc"
        _configure_open_kitchen_mock(ctx2, steps, tmp_path)
        with patch("autoskillit.server._get_ctx", return_value=ctx2):
            result2 = json.loads(await open_kitchen(name="remediation", ctx=ctx2))
        assert result2["success"] is True

        tracker_after = json.loads(tracker_path.read_text())
        assert tracker_after["steps"]["rectify"]["status"] == "complete"
        assert tracker_after["dependencies"].get("review_approach") == ["rectify"]

    @pytest.mark.anyio
    async def test_open_kitchen_auto_init_multi_pipeline(self, tmp_path):
        from unittest.mock import patch

        from autoskillit.recipe.schema import RecipeStep
        from autoskillit.server.tools.tools_execution import _check_pipeline_deps
        from autoskillit.server.tools.tools_kitchen import open_kitchen
        from tests.server.conftest import _make_mock_ctx

        temp_dir = tmp_path / ".autoskillit" / "temp"
        temp_dir.mkdir(parents=True)
        (temp_dir / ".hook_config.json").write_text("{}")

        steps = {
            "rectify": RecipeStep(name="rectify", on_success="review_approach"),
            "review_approach": RecipeStep(name="review_approach", on_success="dry_walkthrough"),
        }

        ctx = _make_mock_ctx()
        ctx.gate.enabled = True
        ctx.gate_infrastructure_ready = True
        ctx.recipe_name = ""
        ctx.kitchen_id = "kitchen-multi"
        _configure_open_kitchen_mock(ctx, steps, tmp_path)

        tracker_dir = tmp_path / ".autoskillit" / "temp" / "pipeline_tracker"
        tracker_dir.mkdir(parents=True, exist_ok=True)
        for oid in ("other-pipeline", "second-pipeline"):
            tracker_dir.joinpath(f"{oid}.json").write_text(
                json.dumps(
                    {
                        "pipeline_id": oid,
                        "kitchen_id": "kitchen-multi",
                        "steps": {
                            "rectify": {"status": "complete"},
                            "review_approach": {"status": "pending"},
                        },
                        "dependencies": {"review_approach": ["rectify"]},
                    }
                )
            )

        with patch("autoskillit.server._get_ctx", return_value=ctx):
            result = json.loads(await open_kitchen(name="remediation", ctx=ctx))
            assert result["success"] is True
            tracker_path = tracker_dir / "kitchen-multi.json"
            assert tracker_path.exists()

            deny_result = _check_pipeline_deps("review_approach", "")

        assert deny_result is not None
        parsed = json.loads(deny_result)
        assert parsed["success"] is False
        assert "order_id" in parsed["error"]


class TestCheckPipelineDepsMultiPipelineFallback:
    @pytest.mark.anyio
    async def test_kitchen_scoped_fallback_requires_order_id_with_multiple_pipelines(
        self, tool_ctx_kitchen_open, tmp_path
    ):
        from autoskillit.server.tools.tools_execution import _check_pipeline_deps

        tool_ctx_kitchen_open.project_dir = tmp_path
        tool_ctx_kitchen_open.kitchen_id = "kitchen-xyz"

        tracker_dir = tmp_path / ".autoskillit" / "temp" / "pipeline_tracker"
        tracker_dir.mkdir(parents=True)
        for oid in ("AB", "CD"):
            tracker_dir.joinpath(f"{oid}.json").write_text(
                json.dumps(
                    {
                        "pipeline_id": oid,
                        "kitchen_id": "kitchen-xyz",
                        "steps": {"a": {"status": "pending"}, "b": {"status": "pending"}},
                        "dependencies": {"b": ["a"]},
                    }
                )
            )

        result = _check_pipeline_deps("b", "")
        assert result is not None
        parsed = json.loads(result)
        assert parsed["success"] is False
        assert "order_id" in parsed["error"]


class TestResolveTrackerOrderIdSingleCandidate:
    def test_kitchen_scoped_fallback_aliases_to_single_candidate(
        self, tool_ctx_kitchen_open, tmp_path
    ):
        """When exactly one non-self tracker matches kitchen_id, resolve to its stem.

        Matches _resolve_order_id_from_kitchen in pipeline_step_guard.py, which
        returns next(iter(active)) in this same single-candidate case.
        """
        from autoskillit.server.tools.tools_pipeline_tracker import (
            ResolvedTracker,
            resolve_tracker_order_id,
        )

        tool_ctx_kitchen_open.project_dir = tmp_path
        tool_ctx_kitchen_open.kitchen_id = "kitchen-xyz"

        tracker_dir = tmp_path / ".autoskillit" / "temp" / "pipeline_tracker"
        tracker_dir.mkdir(parents=True)
        tracker_dir.joinpath("AB.json").write_text(
            json.dumps(
                {
                    "pipeline_id": "AB",
                    "kitchen_id": "kitchen-xyz",
                    "steps": {"a": {"status": "pending"}},
                    "dependencies": {},
                }
            )
        )

        result = resolve_tracker_order_id(tool_ctx_kitchen_open, "")

        assert isinstance(result, ResolvedTracker)
        assert result.order_id == "AB"
