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


def _setup_config_authority_recipe(tool_ctx, recipe):
    """Wire tool_ctx with the given Recipe for config-authority injection tests."""
    from autoskillit.fleet import FleetSemaphore
    from tests.fakes import InMemoryHeadlessExecutor, InMemoryRecipeRepository

    tool_ctx.fleet_lock = FleetSemaphore(max_concurrent=1)
    repo = InMemoryRecipeRepository()
    recipe_info = _make_recipe_info("test-recipe")
    repo.add_recipe("test-recipe", recipe_info)
    repo.add_full_recipe(recipe_info.path, recipe)
    tool_ctx.recipes = repo
    tool_ctx.executor = InMemoryHeadlessExecutor()


class TestConfigAuthoritativeIngredientInjection:
    @pytest.mark.anyio
    async def test_config_authoritative_base_branch_injected_at_dispatch(self, tool_ctx):
        """base_branch with authority='config' is injected from config even when not supplied."""
        from unittest.mock import patch

        from autoskillit.recipe.schema import Recipe, RecipeIngredient, RecipeKind

        _setup_config_authority_recipe(
            tool_ctx,
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
    async def test_config_authoritative_base_branch_overrides_llm_value(self, tool_ctx):
        """base_branch with authority='config' overrides LLM-supplied value."""
        from unittest.mock import patch

        from autoskillit.recipe.schema import Recipe, RecipeIngredient, RecipeKind

        _setup_config_authority_recipe(
            tool_ctx,
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

        from autoskillit.recipe.schema import Recipe, RecipeIngredient, RecipeKind

        _setup_config_authority_recipe(
            tool_ctx,
            Recipe(
                name="test-recipe",
                description="test",
                kind=RecipeKind.STANDARD,
                ingredients={
                    "other_key": RecipeIngredient(description="other", default="x"),
                },
            ),
        )
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

    @pytest.mark.anyio
    async def test_config_authoritative_ingredients_injected_for_all_resolved_keys(self, tool_ctx):
        """All ingredients declared authority='config' receive config values,
        not just base_branch."""
        from unittest.mock import patch

        from autoskillit.recipe.schema import Recipe, RecipeIngredient, RecipeKind

        _setup_config_authority_recipe(
            tool_ctx,
            Recipe(
                name="test-recipe",
                description="test",
                kind=RecipeKind.STANDARD,
                ingredients={
                    "base_branch": RecipeIngredient(
                        description="Merge target", default="", authority="config"
                    ),
                    "source_dir": RecipeIngredient(
                        description="Source directory", default="", authority="config"
                    ),
                    "local_review_rounds": RecipeIngredient(
                        description="Review rounds", default="1", authority="config"
                    ),
                },
            ),
        )
        captured: dict = {}

        def _capture_prompt_builder(**kwargs):
            captured.update(kwargs)
            return "prompt"

        from autoskillit.fleet._api import execute_dispatch

        with patch(
            "autoskillit.config.ingredient_defaults.resolve_ingredient_defaults",
            return_value={
                "base_branch": "develop",
                "source_dir": "/repo/src",
                "local_review_rounds": "3",
            },
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
        assert captured["ingredients"]["source_dir"] == "/repo/src"
        assert captured["ingredients"]["local_review_rounds"] == "3"

    @pytest.mark.anyio
    async def test_dispatch_with_config_authority_recipe_e2e(self, tool_ctx):
        """State snapshot written by execute_dispatch records the config-injected base_branch."""
        import json
        from unittest.mock import patch

        from autoskillit.recipe.schema import Recipe, RecipeIngredient, RecipeKind

        _setup_config_authority_recipe(
            tool_ctx,
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
                prompt_builder=lambda **kw: "prompt",
                quota_checker=_no_sleep_quota_checker,
                quota_refresher=_noop_quota_refresher,
            )

        dispatches_dir = tool_ctx.temp_dir / "dispatches"
        state_files = list(dispatches_dir.glob("*.json"))
        assert len(state_files) == 1, f"Expected 1 state file, found {len(state_files)}"
        state = json.loads(state_files[0].read_text())
        snapshot = state.get("recipe_snapshot") or {}
        effective = snapshot.get("effective_ingredients", {})
        assert effective.get("base_branch") == "develop", (
            "State snapshot should record config value 'develop', got: "
            f"{effective.get('base_branch')!r}"
        )

    @pytest.mark.anyio
    async def test_config_authoritative_key_absent_from_defaults_retains_caller_value(
        self, tool_ctx
    ):
        """When a config-authority key is absent from resolved defaults, the caller-supplied
        value is retained and a warning is logged."""
        from unittest.mock import patch

        import structlog.testing

        from autoskillit.recipe.schema import Recipe, RecipeIngredient, RecipeKind

        _setup_config_authority_recipe(
            tool_ctx,
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
        captured: dict = {}

        def _capture_prompt_builder(**kwargs):
            captured.update(kwargs)
            return "prompt"

        from autoskillit.fleet._api import execute_dispatch

        with structlog.testing.capture_logs() as cap_logs:
            with patch(
                "autoskillit.config.ingredient_defaults.resolve_ingredient_defaults",
                return_value={},  # base_branch absent — simulates resolver not returning the key
            ):
                await execute_dispatch(
                    tool_ctx=tool_ctx,
                    recipe="test-recipe",
                    task="t",
                    ingredients={"base_branch": "caller-supplied"},
                    dispatch_name=None,
                    timeout_sec=None,
                    prompt_builder=_capture_prompt_builder,
                    quota_checker=_no_sleep_quota_checker,
                    quota_refresher=_noop_quota_refresher,
                )

        assert captured["ingredients"]["base_branch"] == "caller-supplied"
        assert any(
            e.get("log_level") == "warning" and "config-authority key" in e.get("event", "")
            for e in cap_logs
        )
