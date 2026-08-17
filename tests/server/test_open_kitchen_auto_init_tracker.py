"""Tests for the open_kitchen auto-init pipeline tracker flow."""

from __future__ import annotations

import json

import pytest

from tests.server._helpers import _with_finalized_projection
from tests.server.conftest import _set_mock_kitchen_transition

pytestmark = [pytest.mark.layer("server"), pytest.mark.small]


def _configure_open_kitchen_mock(ctx, steps, tmp_path):
    from unittest.mock import MagicMock

    ctx.project_dir = tmp_path
    ctx.recipes.load_and_validate.return_value = _with_finalized_projection(
        {
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
    )
    mock_recipe_info = MagicMock()
    mock_recipe_info.path = tmp_path / "remediation.yaml"
    ctx.recipes.find.return_value = mock_recipe_info
    mock_recipe_obj = MagicMock()
    mock_recipe_obj.steps = steps
    mock_recipe_obj.ingredients = {}
    ctx.recipes.load.return_value = mock_recipe_obj


class TestOpenKitchenAutoInitTracker:
    def test_auto_init_releases_authority_when_initialization_raises(self, monkeypatch, tmp_path):
        from autoskillit.recipe.schema import RecipeStep
        from autoskillit.server.tools.tools_kitchen import _auto_init_pipeline_tracker
        from tests.server.conftest import _make_mock_ctx

        ctx = _make_mock_ctx()
        ctx.project_dir = tmp_path
        ctx.kitchen_id = "kitchen-error"
        ctx.active_recipe_steps = {
            "rectify": RecipeStep(name="rectify", on_success="review_approach"),
            "review_approach": RecipeStep(name="review_approach"),
        }

        def _raise(*_args):
            raise RuntimeError("initialization failed")

        monkeypatch.setattr(
            "autoskillit.server.tools.tools_kitchen.initialize_kitchen_tracker", _raise
        )

        with pytest.raises(RuntimeError, match="initialization failed"):
            _auto_init_pipeline_tracker(ctx)

        assert ctx.tracker_leases == {}
        assert ctx.kitchen_tracker_key is None

    def test_auto_init_preserves_corrupt_authority(self, tmp_path):
        from autoskillit.recipe.schema import RecipeStep
        from autoskillit.server.tools.tools_kitchen import (
            _auto_init_pipeline_tracker,
        )
        from tests.server.conftest import _make_mock_ctx

        ctx = _make_mock_ctx()
        ctx.project_dir = tmp_path
        ctx.kitchen_id = "kitchen-corrupt"
        ctx.active_recipe_steps = {
            "rectify": RecipeStep(name="rectify", on_success="review_approach"),
            "review_approach": RecipeStep(name="review_approach"),
        }
        tracker_path = (
            tmp_path / ".autoskillit" / "temp" / "pipeline_tracker" / "kitchen-corrupt.json"
        )
        tracker_path.parent.mkdir(parents=True)
        tracker_path.write_bytes(b"{not-json")
        error = _auto_init_pipeline_tracker(ctx)

        assert error is not None
        assert "invalid" in error
        assert tracker_path.read_bytes() == b"{not-json"
        assert ctx.tracker_leases == {}
        assert ctx.kitchen_tracker_key is None

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
        _set_mock_kitchen_transition(ctx, kitchen_id=ctx.kitchen_id)
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
        _set_mock_kitchen_transition(ctx1, kitchen_id=ctx1.kitchen_id)
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
        _set_mock_kitchen_transition(ctx2, kitchen_id=ctx2.kitchen_id)
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
        _set_mock_kitchen_transition(ctx, kitchen_id=ctx.kitchen_id)
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

            from autoskillit.server.tools.tools_execution import _select_tracker_authority

            _target, authority, key, _lease = _select_tracker_authority(ctx, "")
            try:
                deny_result = _check_pipeline_deps("review_approach", authority)
            finally:
                if key is not None:
                    from autoskillit.server.tools.tools_pipeline_tracker import (
                        _release_context_tracker,
                    )

                    _release_context_tracker(ctx, key)

        assert deny_result is not None
        parsed = json.loads(deny_result)
        assert parsed["success"] is False
        assert "Pipeline 'kitchen-multi'" in parsed["error"]
        assert "other-pipeline" not in parsed["error"]
