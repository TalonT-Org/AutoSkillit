"""Failed run_skill bindings cannot be represented by incomplete snapshots."""

from __future__ import annotations

import pytest

from autoskillit.recipe._binding import bind_recipe
from autoskillit.recipe.schema import Recipe, RecipeStep
from autoskillit.server.recipe._recipe_execution import build_recipe_execution_snapshot

pytestmark = [pytest.mark.layer("server"), pytest.mark.small]

_HASH = "sha256:" + "0" * 64


def test_snapshot_rejects_failing_run_skill_binding() -> None:
    recipe = Recipe(
        name="r",
        description="d",
        steps={
            "run_bem": RecipeStep(
                name="run_bem",
                tool="run_skill",
                with_args={"skill_command": "/autoskillit:build-execution-map"},
            )
        },
    )
    projection = bind_recipe(recipe)

    with pytest.raises(AssertionError, match=r"run_bem.*missing_tool_parameter"):
        build_recipe_execution_snapshot(
            recipe_name="r",
            content_hash=_HASH,
            composite_hash=_HASH,
            projection=projection,
        )
