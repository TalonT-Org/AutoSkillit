"""Authority-boundary tests for food truck prompt overrides."""

from __future__ import annotations

import json
import re

import pytest

from autoskillit.core import SERVER_AUTHORITATIVE_INGREDIENTS
from autoskillit.core._plugin_ids import DIRECT_PREFIX
from autoskillit.fleet._prompts import _build_food_truck_prompt
from autoskillit.fleet.campaign_state.state_effects import DispatchProvenanceTracker
from autoskillit.fleet.dispatch._validation import RecipeContext, run_pre_launch_gating
from autoskillit.recipe.repository import DefaultRecipeRepository

pytestmark = [pytest.mark.layer("fleet"), pytest.mark.medium, pytest.mark.feature("fleet")]


@pytest.mark.parametrize("ingredient_name", ("dispatch_id", "is_fleet_dispatch", "base_branch"))
def test_prompt_builder_refuses_server_authoritative_keys(ingredient_name: str) -> None:
    """The child-facing prompt cannot carry values owned by the server."""
    with pytest.raises(ValueError, match=ingredient_name):
        _build_food_truck_prompt(
            recipe="test-recipe",
            task="test task",
            ingredients={ingredient_name: "caller-value"},
            mcp_prefix=DIRECT_PREFIX,
            dispatch_id="test-dispatch",
            campaign_id="test-campaign",
            l3_timeout_sec=300,
        )


@pytest.mark.anyio
@pytest.mark.parametrize(
    "recipe_name",
    ("implementation", "remediation", "merge-prs", "implementation-groups"),
)
async def test_h1_overrides_from_real_phase_a_pass_the_open_kitchen_gate(
    tool_ctx, recipe_name: str
) -> None:
    """Bundled Phase A output omits every server-authoritative override."""
    tool_ctx.recipes = DefaultRecipeRepository()
    recipe_info = tool_ctx.recipes.find(recipe_name, tool_ctx.project_dir)
    assert recipe_info is not None
    recipe = tool_ctx.recipes.load(recipe_info.path)
    server_authoritative_overrides = {
        ingredient_name: "llm-supplied"
        for ingredient_name in set(recipe.ingredients) & SERVER_AUTHORITATIVE_INGREDIENTS
    }
    assert server_authoritative_overrides

    gating_result = await run_pre_launch_gating(
        tool_ctx=tool_ctx,
        recipe=recipe_name,
        task="Exercise bundled prompt authority",
        ingredients={
            "task": "Exercise bundled prompt authority",
            **server_authoritative_overrides,
        },
        dispatch_name=None,
        provenance=DispatchProvenanceTracker(),
    )

    assert isinstance(gating_result, RecipeContext)
    prompt = _build_food_truck_prompt(
        recipe=recipe_name,
        task="Exercise bundled prompt authority",
        ingredients=gating_result.effective_ingredients,
        mcp_prefix=DIRECT_PREFIX,
        dispatch_id="test-dispatch",
        campaign_id="test-campaign",
        l3_timeout_sec=300,
    )
    match = re.search(
        rf"open_kitchen\(name='{re.escape(recipe_name)}', overrides=(\{{.*\}})\)", prompt
    )
    assert match is not None

    overrides = json.loads(match.group(1))
    assert set(overrides) & SERVER_AUTHORITATIVE_INGREDIENTS == set()
