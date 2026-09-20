"""Missing required ingredient validation tests for fleet dispatch."""

from __future__ import annotations

import pytest

from tests.fleet._helpers import (
    _make_recipe_info,
    _noop_quota_refresher,
    _run,
)

pytestmark = [pytest.mark.layer("fleet"), pytest.mark.small, pytest.mark.feature("fleet")]


def _setup_dispatch_with_ingredients(tool_ctx, ingredients: dict):
    """Wire tool_ctx with a recipe that has specific ingredients."""
    from autoskillit.core import DefaultManagedWorkerCapacity
    from autoskillit.recipe.schema import Recipe, RecipeIngredient, RecipeKind
    from tests.fakes import InMemoryHeadlessExecutor, InMemoryRecipeRepository

    tool_ctx.worker_capacity = DefaultManagedWorkerCapacity(max_concurrent=1)
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
            quota_refresher=_noop_quota_refresher,
        )

        assert "task" not in captured["ingredients"]


def _setup_config_authority_recipe(tool_ctx, recipe):
    """Wire tool_ctx with the given Recipe for config-authority injection tests."""
    from autoskillit.core import DefaultManagedWorkerCapacity
    from tests.fakes import InMemoryHeadlessExecutor, InMemoryRecipeRepository

    tool_ctx.worker_capacity = DefaultManagedWorkerCapacity(max_concurrent=1)
    repo = InMemoryRecipeRepository()
    recipe_info = _make_recipe_info("test-recipe")
    repo.add_recipe("test-recipe", recipe_info)
    repo.add_full_recipe(recipe_info.path, recipe)
    tool_ctx.recipes = repo
    tool_ctx.executor = InMemoryHeadlessExecutor()


class TestServerAuthoritativeOverrides:
    @pytest.mark.anyio
    async def test_config_authoritative_base_branch_from_llm_is_stripped(self, tool_ctx):
        """A caller-supplied base_branch is removed before prompt rendering."""
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
        captured = {}

        def _capture_prompt_builder(**kwargs):
            captured.update(kwargs)
            return "prompt"

        from autoskillit.fleet._api import execute_dispatch

        with structlog.testing.capture_logs() as cap_logs:
            await execute_dispatch(
                tool_ctx=tool_ctx,
                recipe="test-recipe",
                task="t",
                ingredients={"base_branch": "main"},
                dispatch_name=None,
                timeout_sec=None,
                prompt_builder=_capture_prompt_builder,
                quota_refresher=_noop_quota_refresher,
            )

        assert "base_branch" not in captured["ingredients"]
        stripped_events = [
            event
            for event in cap_logs
            if event.get("event") == "fleet_dispatch_server_authoritative_overrides_stripped"
        ]
        assert len(stripped_events) == 1
        assert stripped_events[0]["keys"] == ["base_branch"]

    @pytest.mark.anyio
    async def test_config_authoritative_injection_skips_undeclared_ingredients(self, tool_ctx):
        """Stripping touches only SERVER_AUTHORITATIVE_INGREDIENTS names."""
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

        await execute_dispatch(
            tool_ctx=tool_ctx,
            recipe="test-recipe",
            task="t",
            ingredients={"base_branch": "main", "other_key": "caller-supplied"},
            dispatch_name=None,
            timeout_sec=None,
            prompt_builder=_capture_prompt_builder,
            quota_refresher=_noop_quota_refresher,
        )

        assert "base_branch" not in captured["ingredients"]
        assert captured["ingredients"]["other_key"] == "caller-supplied"

    @pytest.mark.anyio
    async def test_server_authoritative_overrides_are_stripped_before_prompt(self, tool_ctx):
        """Fleet removes server-authoritative values without resolving defaults."""
        from unittest.mock import patch

        import autoskillit.config.ingredient_defaults as ingredient_defaults
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
                    "dispatch_id": RecipeIngredient(
                        description="Dispatch identity",
                        default="",
                        authority="config",
                        hidden=True,
                    ),
                    "is_fleet_dispatch": RecipeIngredient(
                        description="Fleet dispatch flag",
                        default="",
                        authority="config",
                        hidden=True,
                    ),
                },
            ),
        )
        captured: dict = {}

        def _capture_prompt_builder(**kwargs):
            captured.update(kwargs)
            return "prompt"

        from autoskillit.fleet._api import execute_dispatch

        with patch.object(
            ingredient_defaults,
            "resolve_ingredient_defaults",
            side_effect=AssertionError("Phase A must not resolve defaults"),
        ):
            await execute_dispatch(
                tool_ctx=tool_ctx,
                recipe="test-recipe",
                task="t",
                ingredients={
                    "base_branch": "main",
                    "local_review_rounds": "9",
                    "dispatch_id": "stale",
                    "is_fleet_dispatch": "false",
                    "source_dir": "/repo/src",
                },
                dispatch_name=None,
                timeout_sec=None,
                prompt_builder=_capture_prompt_builder,
                quota_refresher=_noop_quota_refresher,
            )

        for key in ("base_branch", "local_review_rounds", "dispatch_id", "is_fleet_dispatch"):
            assert key not in captured["ingredients"]
        assert captured["ingredients"]["source_dir"] == "/repo/src"

    @pytest.mark.anyio
    async def test_dispatch_with_config_authority_recipe_e2e(self, tool_ctx):
        """State snapshot written by execute_dispatch records filtered overrides."""
        import json

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

        await execute_dispatch(
            tool_ctx=tool_ctx,
            recipe="test-recipe",
            task="t",
            ingredients={"base_branch": "main"},
            dispatch_name=None,
            timeout_sec=None,
            prompt_builder=lambda **kw: "prompt",
            quota_refresher=_noop_quota_refresher,
        )

        dispatches_dir = tool_ctx.temp_dir / "dispatches"
        state_files = list(dispatches_dir.glob("*.json"))
        assert len(state_files) == 1, f"Expected 1 state file, found {len(state_files)}"
        state = json.loads(state_files[0].read_text())
        snapshot = state.get("recipe_snapshot") or {}
        effective = snapshot.get("effective_ingredients", {})
        assert "base_branch" not in effective

    @pytest.mark.anyio
    async def test_config_authoritative_key_absent_from_defaults_retains_caller_value(
        self, tool_ctx
    ):
        """A caller-sovereign key passes through without a stripping warning."""
        from autoskillit.recipe.schema import Recipe, RecipeIngredient, RecipeKind

        _setup_config_authority_recipe(
            tool_ctx,
            Recipe(
                name="test-recipe",
                description="test",
                kind=RecipeKind.STANDARD,
                ingredients={
                    "truly_unknown_key": RecipeIngredient(
                        description="Truly absent", default="", authority="config"
                    )
                },
            ),
        )
        captured: dict = {}

        def _capture_prompt_builder(**kwargs):
            captured.update(kwargs)
            return "prompt"

        import structlog.testing

        from autoskillit.fleet._api import execute_dispatch

        with structlog.testing.capture_logs() as cap_logs:
            await execute_dispatch(
                tool_ctx=tool_ctx,
                recipe="test-recipe",
                task="t",
                ingredients={"truly_unknown_key": "caller-supplied"},
                dispatch_name=None,
                timeout_sec=None,
                prompt_builder=_capture_prompt_builder,
                quota_refresher=_noop_quota_refresher,
            )

        assert captured["ingredients"]["truly_unknown_key"] == "caller-supplied"
        assert not any(
            event.get("event") == "fleet_dispatch_server_authoritative_overrides_stripped"
            for event in cap_logs
        )

    def test_strip_server_authoritative_overrides_source_dir_preserves_caller_local_path(self):
        """source_dir is caller-sovereign and retains a caller-supplied local path."""
        from autoskillit.config import strip_server_authoritative_overrides

        result, stripped = strip_server_authoritative_overrides(
            {"source_dir": "/home/user/myproject"}
        )

        assert result["source_dir"] == "/home/user/myproject"
        assert not stripped

    def test_strip_server_authoritative_overrides_source_dir_not_injected_when_absent(self):
        """source_dir remains absent when the caller does not supply it."""
        from autoskillit.config import strip_server_authoritative_overrides

        result, stripped = strip_server_authoritative_overrides({})

        assert "source_dir" not in result
        assert not stripped
