"""Recipe kind dispatch gate tests for fleet dispatch."""

from __future__ import annotations

import pytest

from tests.fleet._helpers import _make_recipe_info, _run

pytestmark = [pytest.mark.layer("fleet"), pytest.mark.small, pytest.mark.feature("fleet")]


class TestRecipeKindDispatchGate:
    """Verify that dispatch gate accepts/rejects by RecipeKind."""

    def _setup_food_truck_recipe(self, tool_ctx):
        """Wire tool_ctx with a food-truck kind recipe."""
        from autoskillit.fleet import FleetSemaphore
        from autoskillit.recipe.schema import Recipe, RecipeKind
        from tests.fakes import InMemoryHeadlessExecutor, InMemoryRecipeRepository

        tool_ctx.fleet_lock = FleetSemaphore(max_concurrent=1)
        repo = InMemoryRecipeRepository()
        recipe_info = _make_recipe_info("test-recipe")
        repo.add_recipe("test-recipe", recipe_info)
        repo.add_full_recipe(
            recipe_info.path,
            Recipe(
                name="test-recipe",
                description="test",
                kind=RecipeKind.FOOD_TRUCK,
                ingredients={},
                requires_packs=[],
            ),
        )
        tool_ctx.recipes = repo
        tool_ctx.executor = InMemoryHeadlessExecutor()

    def _setup_campaign_recipe(self, tool_ctx):
        """Wire tool_ctx with a campaign kind recipe."""
        from autoskillit.fleet import FleetSemaphore
        from autoskillit.recipe.schema import Recipe, RecipeKind
        from tests.fakes import InMemoryHeadlessExecutor, InMemoryRecipeRepository

        tool_ctx.fleet_lock = FleetSemaphore(max_concurrent=1)
        repo = InMemoryRecipeRepository()
        recipe_info = _make_recipe_info("test-recipe")
        repo.add_recipe("test-recipe", recipe_info)
        repo.add_full_recipe(
            recipe_info.path,
            Recipe(
                name="test-recipe",
                description="test",
                kind=RecipeKind.CAMPAIGN,
                ingredients={},
                requires_packs=[],
            ),
        )
        tool_ctx.recipes = repo
        tool_ctx.executor = InMemoryHeadlessExecutor()

    @pytest.mark.anyio
    async def test_food_truck_dispatchable(self, tool_ctx, monkeypatch):
        """T4: FOOD_TRUCK kind is accepted by the dispatch gate (not rejected)."""
        self._setup_food_truck_recipe(tool_ctx)

        result = await _run(tool_ctx)
        assert result.get("error") != "fleet_invalid_recipe_kind"

    @pytest.mark.anyio
    async def test_campaign_kind_still_rejected_by_dispatch(self, tool_ctx, monkeypatch):
        """T5: CAMPAIGN kind is still rejected by the dispatch gate."""
        self._setup_campaign_recipe(tool_ctx)

        result = await _run(tool_ctx)
        assert result["success"] is False
        assert result["error"] == "fleet_invalid_recipe_kind"
