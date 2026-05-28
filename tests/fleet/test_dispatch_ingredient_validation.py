"""Missing required ingredient validation tests for fleet dispatch."""

from __future__ import annotations

import pytest

from tests.fleet._helpers import (
    _make_recipe_info,
    _no_sleep_quota_checker,
    _noop_quota_refresher,
    _run,
)

pytestmark = [pytest.mark.layer("fleet"), pytest.mark.small, pytest.mark.feature("fleet")]


def _setup_dispatch_with_ingredients(tool_ctx, ingredients: dict):
    """Wire tool_ctx with a recipe that has specific ingredients."""
    from autoskillit.fleet import FleetSemaphore
    from autoskillit.recipe.schema import Recipe, RecipeIngredient, RecipeKind
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
            kind=RecipeKind.STANDARD,
            ingredients={
                k: RecipeIngredient(description=f"desc-{k}", **v) for k, v in ingredients.items()
            },
        ),
    )
    tool_ctx.recipes = repo
    tool_ctx.executor = InMemoryHeadlessExecutor()


class TestMissingRequiredIngredient:
    @pytest.mark.anyio
    async def test_dispatch_rejects_missing_required_ingredient(self, tool_ctx):
        """Required ingredient with no default → FLEET_MISSING_INGREDIENT."""
        _setup_dispatch_with_ingredients(
            tool_ctx, {"api_key": {"required": True, "default": None}}
        )

        result = await _run(tool_ctx, ingredients={})
        assert result["success"] is False
        assert result["error"] == "fleet_missing_ingredient"

    @pytest.mark.anyio
    async def test_dispatch_allows_required_ingredient_when_supplied(self, tool_ctx):
        """A required ingredient that IS supplied passes validation."""
        _setup_dispatch_with_ingredients(
            tool_ctx, {"api_key": {"required": True, "default": None}}
        )

        result = await _run(tool_ctx, ingredients={"api_key": "secret"})
        assert result.get("error") != "fleet_missing_ingredient"

    @pytest.mark.anyio
    async def test_dispatch_allows_required_ingredient_with_default(self, tool_ctx):
        """A required ingredient with a non-None default passes even when not supplied."""
        _setup_dispatch_with_ingredients(
            tool_ctx, {"api_key": {"required": True, "default": "fallback"}}
        )

        result = await _run(tool_ctx, ingredients={})
        assert result.get("error") != "fleet_missing_ingredient"

    @pytest.mark.anyio
    async def test_dispatch_lists_all_missing_required_ingredients(self, tool_ctx):
        """When multiple required ingredients are missing, all are listed."""
        _setup_dispatch_with_ingredients(
            tool_ctx,
            {
                "key_a": {"required": True, "default": None},
                "key_b": {"required": True, "default": None},
            },
        )

        result = await _run(tool_ctx, ingredients={})
        assert result["success"] is False
        assert result["error"] == "fleet_missing_ingredient"
        assert "key_a" in result["user_visible_message"]
        assert "key_b" in result["user_visible_message"]

    @pytest.mark.anyio
    async def test_dispatch_ignores_optional_missing_ingredients(self, tool_ctx):
        """Optional ingredients (required=False) don't trigger missing-ingredient errors."""
        _setup_dispatch_with_ingredients(
            tool_ctx, {"optional_key": {"required": False, "default": None}}
        )

        result = await _run(tool_ctx, ingredients={})
        assert result.get("error") != "fleet_missing_ingredient"

    @pytest.mark.anyio
    async def test_task_auto_injected_from_top_level_param(self, tool_ctx):
        """top-level task param auto-injects into effective_ingredients when recipe declares it."""
        _setup_dispatch_with_ingredients(tool_ctx, {"task": {"required": True, "default": None}})

        result = await _run(tool_ctx, ingredients={})
        assert result.get("error") != "fleet_missing_ingredient"

    @pytest.mark.anyio
    async def test_explicit_ingredient_task_overrides_top_level(self, tool_ctx):
        """Explicit ingredients['task'] takes precedence over top-level task param."""
        _setup_dispatch_with_ingredients(tool_ctx, {"task": {"required": True, "default": None}})

        captured = {}

        def _capture_prompt_builder(**kwargs):
            captured.update(kwargs)
            return "prompt"

        from autoskillit.fleet._api import execute_dispatch

        await execute_dispatch(
            tool_ctx=tool_ctx,
            recipe="test-recipe",
            task="top-level-value",
            ingredients={"task": "override-value"},
            dispatch_name=None,
            timeout_sec=None,
            prompt_builder=_capture_prompt_builder,
            quota_checker=_no_sleep_quota_checker,
            quota_refresher=_noop_quota_refresher,
        )

        assert captured["ingredients"]["task"] == "override-value"

    @pytest.mark.anyio
    async def test_task_not_injected_when_not_declared_ingredient(self, tool_ctx):
        """Top-level task is NOT injected when recipe has no 'task' ingredient key."""
        _setup_dispatch_with_ingredients(
            tool_ctx, {"other_key": {"required": False, "default": "x"}}
        )

        captured = {}

        def _capture_prompt_builder(**kwargs):
            captured.update(kwargs)
            return "prompt"

        from autoskillit.fleet._api import execute_dispatch

        await execute_dispatch(
            tool_ctx=tool_ctx,
            recipe="test-recipe",
            task="some-task",
            ingredients={},
            dispatch_name=None,
            timeout_sec=None,
            prompt_builder=_capture_prompt_builder,
            quota_checker=_no_sleep_quota_checker,
            quota_refresher=_noop_quota_refresher,
        )

        assert "task" not in captured["ingredients"]


class TestConfigAuthoritativeIngredientInjection:
    @pytest.mark.anyio
    async def test_config_authoritative_base_branch_injected_at_dispatch(
        self, tool_ctx, monkeypatch
    ):
        """base_branch with authority='config' is injected from config even when not supplied."""
        from unittest.mock import patch

        from autoskillit.fleet import FleetSemaphore
        from autoskillit.recipe.schema import Recipe, RecipeIngredient, RecipeKind
        from tests.fakes import InMemoryHeadlessExecutor, InMemoryRecipeRepository
        from tests.fleet._helpers import (
            _make_recipe_info,
            _no_sleep_quota_checker,
            _noop_quota_refresher,
        )

        tool_ctx.fleet_lock = FleetSemaphore(max_concurrent=1)
        repo = InMemoryRecipeRepository()
        recipe_info = _make_recipe_info("test-recipe")
        repo.add_recipe("test-recipe", recipe_info)
        repo.add_full_recipe(
            recipe_info.path,
            Recipe(
                name="test-recipe",
                description="test",
                kind=RecipeKind.STANDARD,
                ingredients={
                    "base_branch": RecipeIngredient(
                        description="Merge target", default="", authority="config"
                    )
                },
            ),
        )
        tool_ctx.recipes = repo
        tool_ctx.executor = InMemoryHeadlessExecutor()

        captured = {}

        def _capture_prompt_builder(**kwargs):
            captured.update(kwargs)
            return "prompt"

        from autoskillit.fleet._api import execute_dispatch

        with patch(
            "autoskillit.config.ingredient_defaults.resolve_ingredient_defaults",
            return_value={"base_branch": "develop"},
        ):
            await execute_dispatch(
                tool_ctx=tool_ctx,
                recipe="test-recipe",
                task="t",
                ingredients={},
                dispatch_name=None,
                timeout_sec=None,
                prompt_builder=_capture_prompt_builder,
                quota_checker=_no_sleep_quota_checker,
                quota_refresher=_noop_quota_refresher,
            )

        assert captured["ingredients"]["base_branch"] == "develop"

    @pytest.mark.anyio
    async def test_config_authoritative_base_branch_overrides_llm_value(
        self, tool_ctx, monkeypatch
    ):
        """base_branch with authority='config' overrides LLM-supplied value."""
        from unittest.mock import patch

        from autoskillit.fleet import FleetSemaphore
        from autoskillit.recipe.schema import Recipe, RecipeIngredient, RecipeKind
        from tests.fakes import InMemoryHeadlessExecutor, InMemoryRecipeRepository
        from tests.fleet._helpers import (
            _make_recipe_info,
            _no_sleep_quota_checker,
            _noop_quota_refresher,
        )

        tool_ctx.fleet_lock = FleetSemaphore(max_concurrent=1)
        repo = InMemoryRecipeRepository()
        recipe_info = _make_recipe_info("test-recipe")
        repo.add_recipe("test-recipe", recipe_info)
        repo.add_full_recipe(
            recipe_info.path,
            Recipe(
                name="test-recipe",
                description="test",
                kind=RecipeKind.STANDARD,
                ingredients={
                    "base_branch": RecipeIngredient(
                        description="Merge target", default="", authority="config"
                    )
                },
            ),
        )
        tool_ctx.recipes = repo
        tool_ctx.executor = InMemoryHeadlessExecutor()

        captured = {}

        def _capture_prompt_builder(**kwargs):
            captured.update(kwargs)
            return "prompt"

        from autoskillit.fleet._api import execute_dispatch

        with patch(
            "autoskillit.config.ingredient_defaults.resolve_ingredient_defaults",
            return_value={"base_branch": "develop"},
        ):
            await execute_dispatch(
                tool_ctx=tool_ctx,
                recipe="test-recipe",
                task="t",
                ingredients={"base_branch": "main"},
                dispatch_name=None,
                timeout_sec=None,
                prompt_builder=_capture_prompt_builder,
                quota_checker=_no_sleep_quota_checker,
                quota_refresher=_noop_quota_refresher,
            )

        assert captured["ingredients"]["base_branch"] == "develop"

    @pytest.mark.anyio
    async def test_config_authoritative_injection_skips_undeclared_ingredients(self, tool_ctx):
        """Config injection only applies to ingredients the recipe declares."""
        from unittest.mock import patch

        from autoskillit.fleet import FleetSemaphore
        from autoskillit.recipe.schema import Recipe, RecipeIngredient, RecipeKind
        from tests.fakes import InMemoryHeadlessExecutor, InMemoryRecipeRepository
        from tests.fleet._helpers import (
            _make_recipe_info,
            _no_sleep_quota_checker,
            _noop_quota_refresher,
        )

        tool_ctx.fleet_lock = FleetSemaphore(max_concurrent=1)
        repo = InMemoryRecipeRepository()
        recipe_info = _make_recipe_info("test-recipe")
        repo.add_recipe("test-recipe", recipe_info)
        repo.add_full_recipe(
            recipe_info.path,
            Recipe(
                name="test-recipe",
                description="test",
                kind=RecipeKind.STANDARD,
                ingredients={
                    "other_key": RecipeIngredient(description="other", default="x"),
                },
            ),
        )
        tool_ctx.recipes = repo
        tool_ctx.executor = InMemoryHeadlessExecutor()

        captured = {}

        def _capture_prompt_builder(**kwargs):
            captured.update(kwargs)
            return "prompt"

        from autoskillit.fleet._api import execute_dispatch

        with patch(
            "autoskillit.config.ingredient_defaults.resolve_ingredient_defaults",
            return_value={"base_branch": "develop"},
        ):
            await execute_dispatch(
                tool_ctx=tool_ctx,
                recipe="test-recipe",
                task="t",
                ingredients={},
                dispatch_name=None,
                timeout_sec=None,
                prompt_builder=_capture_prompt_builder,
                quota_checker=_no_sleep_quota_checker,
                quota_refresher=_noop_quota_refresher,
            )

        assert "base_branch" not in captured["ingredients"]
