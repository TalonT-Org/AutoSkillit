"""Server installation coverage for finalized recipe projections."""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from autoskillit.server.tools.tools_kitchen import lock_ingredients, open_kitchen

pytestmark = [pytest.mark.layer("server"), pytest.mark.medium]

_RECIPE_NAME = "composed-projection-parent"
_COMPOSED_CHILD_STEP = "composed_projection_child_child_step"


def _request_context() -> MagicMock:
    ctx = MagicMock()
    ctx.enable_components = AsyncMock()
    ctx.disable_components = AsyncMock()
    ctx.reset_visibility = AsyncMock()
    return ctx


def _install_composed_recipe(tmp_path: Path) -> None:
    fixture_dir = Path(__file__).parents[1] / "recipe" / "fixtures"
    recipe_dir = tmp_path / ".autoskillit" / "recipes"
    recipe_dir.mkdir(parents=True)
    shutil.copy2(
        fixture_dir / "composed_projection_parent.yaml",
        recipe_dir / "composed-projection-parent.yaml",
    )
    sub_recipe_dir = recipe_dir / "sub-recipes"
    sub_recipe_dir.mkdir()
    shutil.copy2(
        fixture_dir / "composed_projection_child.yaml",
        sub_recipe_dir / "composed-projection-child.yaml",
    )


def test_failed_serve_clears_all_cached_projection_authority() -> None:
    from autoskillit.server.tools.tools_kitchen import _clear_active_recipe_projection

    tool_ctx = MagicMock()
    tool_ctx.active_recipe_projection = object()
    tool_ctx.active_recipe_steps = {"stale": object()}
    tool_ctx.active_recipe_ingredients = frozenset({"stale"})

    _clear_active_recipe_projection(tool_ctx)

    assert tool_ctx.active_recipe_projection is None
    assert tool_ctx.active_recipe_steps == {}
    assert tool_ctx.active_recipe_ingredients == frozenset()


@pytest.mark.anyio
@pytest.mark.parametrize("deferred_recall", [False, True], ids=["cold-open", "deferred-recall"])
async def test_open_kitchen_installs_steps_from_the_finalized_projection(
    tool_ctx_kitchen_open,
    tmp_path: Path,
    deferred_recall: bool,
) -> None:
    """Both serve paths install exactly the projection's executable step map."""
    _install_composed_recipe(tmp_path)
    tool_ctx_kitchen_open.project_dir = tmp_path
    tool_ctx_kitchen_open.recipe_name = _RECIPE_NAME if deferred_recall else ""

    result = json.loads(await open_kitchen(name=_RECIPE_NAME, ctx=_request_context()))

    assert result["success"] is True, result
    projection = tool_ctx_kitchen_open.active_recipe_projection
    assert projection is not None
    assert tuple(tool_ctx_kitchen_open.active_recipe_steps) == projection.ordered_step_names
    assert tool_ctx_kitchen_open.active_recipe_steps == {
        step.name: step for step in projection.ordered_steps
    }
    for step_name in projection.ordered_step_names:
        active_step = tool_ctx_kitchen_open.active_recipe_steps[step_name]
        assert active_step is projection.for_step(step_name)
    assert all(
        tool_ctx_kitchen_open.active_recipe_steps[edge.source] is projection.for_step(edge.source)
        for edge in projection.ordered_flow_edges
    )


@pytest.mark.anyio
async def test_composed_recipe_installs_child_step_and_ingredient(
    tool_ctx_kitchen_open,
    tmp_path: Path,
) -> None:
    """The server consumes the composed projection, not an independent raw recipe read."""
    _install_composed_recipe(tmp_path)
    tool_ctx_kitchen_open.project_dir = tmp_path
    tool_ctx_kitchen_open.recipe_name = ""

    result = json.loads(await open_kitchen(name=_RECIPE_NAME, ctx=_request_context()))

    assert result["success"] is True, result
    assert _COMPOSED_CHILD_STEP in tool_ctx_kitchen_open.active_recipe_steps
    assert "child_only" in tool_ctx_kitchen_open.active_recipe_ingredients

    lock_result = json.loads(
        await lock_ingredients(
            locked={"child_only": "child-value"},
            pipeline_id="composed-projection",
        )
    )
    assert lock_result["success"] is True, lock_result
    assert lock_result["locked"] == {"child_only": "child-value"}
