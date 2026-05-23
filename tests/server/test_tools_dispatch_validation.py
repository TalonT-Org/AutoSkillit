"""Tests for dispatch_food_truck validation: gates, input, and semantic validation."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from autoskillit.fleet import FleetSemaphore
from tests.fakes import InMemoryHeadlessExecutor, InMemoryRecipeRepository
from tests.server._helpers import _make_recipe_info, _make_standard_recipe

pytestmark = [pytest.mark.layer("server"), pytest.mark.medium, pytest.mark.feature("fleet")]


def _simple_prompt_builder(**kwargs) -> str:
    """Minimal prompt builder for tests — avoids CLI imports."""
    return f"prompt-for-{kwargs.get('recipe', 'unknown')}"


async def _no_sleep_quota_checker(config, **kwargs) -> dict:
    """Quota checker stub: always returns no-sleep result."""
    return {
        "should_sleep": False,
        "sleep_seconds": 0,
        "utilization": None,
        "resets_at": None,
        "window_name": None,
    }


async def _noop_quota_refresher(config, **kwargs) -> None:
    """Quota refresher stub: no-op."""


class TestDispatchFoodTruckGates:
    @pytest.mark.anyio
    async def test_dispatch_food_truck_hard_refusal_headless(
        self, tool_ctx_kitchen_open, monkeypatch
    ):
        """AUTOSKILLIT_HEADLESS=1 → fleet_hard_refusal_headless, regardless of SESSION_TYPE."""
        monkeypatch.setenv("AUTOSKILLIT_HEADLESS", "1")
        from autoskillit.server.tools.tools_fleet_dispatch import dispatch_food_truck

        result = json.loads(await dispatch_food_truck(recipe="r", task="t"))
        assert result["success"] is False
        assert result["subtype"] == "headless_error"

    @pytest.mark.anyio
    async def test_dispatch_food_truck_requires_fleet_session_type(
        self, tool_ctx_kitchen_open, monkeypatch
    ):
        """Non-fleet session type → headless_error, even for interactive callers."""
        monkeypatch.setenv("AUTOSKILLIT_SESSION_TYPE", "orchestrator")
        monkeypatch.delenv("AUTOSKILLIT_HEADLESS", raising=False)
        from autoskillit.server.tools.tools_fleet_dispatch import dispatch_food_truck

        result = json.loads(await dispatch_food_truck(recipe="r", task="t"))
        assert result["success"] is False
        assert result["subtype"] == "headless_error"

    @pytest.mark.anyio
    async def test_dispatch_food_truck_requires_kitchen_open(self, tool_ctx, monkeypatch):
        """Kitchen closed → gate_error_result JSON."""
        from autoskillit.pipeline.gate import DefaultGateState
        from autoskillit.server.tools.tools_fleet_dispatch import dispatch_food_truck

        tool_ctx.gate = DefaultGateState(enabled=False)
        result = json.loads(await dispatch_food_truck(recipe="r", task="t"))
        assert result["success"] is False
        assert result["subtype"] == "gate_error"

    @pytest.mark.anyio
    async def test_dispatch_food_truck_parallel_refused_when_locked(
        self, tool_ctx, monkeypatch, tmp_path
    ):
        """fleet_lock.at_capacity() == True → fleet_parallel_refused error."""
        from autoskillit.fleet._api import execute_dispatch

        lock = FleetSemaphore(max_concurrent=1)
        await lock.acquire()  # lock it
        tool_ctx.fleet_lock = lock

        _dispatch_result = await execute_dispatch(
            tool_ctx=tool_ctx,
            recipe="r",
            task="t",
            ingredients=None,
            dispatch_name=None,
            timeout_sec=None,
            prompt_builder=_simple_prompt_builder,
            quota_checker=_no_sleep_quota_checker,
            quota_refresher=_noop_quota_refresher,
        )
        result = json.loads(_dispatch_result.outcome.to_envelope())
        assert result["success"] is False
        assert result["error"] == "fleet_parallel_refused"

    @pytest.mark.anyio
    async def test_dispatch_food_truck_refuses_when_fleet_feature_disabled(
        self, tool_ctx_kitchen_open, monkeypatch
    ):
        """features.fleet: false in config → fleet_feature_disabled, regardless of gate state."""
        import dataclasses

        from autoskillit.server.tools.tools_fleet_dispatch import dispatch_food_truck

        monkeypatch.setenv("AUTOSKILLIT_SESSION_TYPE", "fleet")
        # Gate is open (fleet session already booted), env var absent
        # Only config file has fleet disabled
        monkeypatch.delenv("AUTOSKILLIT_FEATURES__FLEET", raising=False)
        tool_ctx_kitchen_open.config = dataclasses.replace(
            tool_ctx_kitchen_open.config, features={"fleet": False}
        )

        result = json.loads(await dispatch_food_truck(recipe="r", task="t"))
        assert result["success"] is False
        assert result["error"] == "fleet_feature_disabled"


class TestDispatchFoodTruckValidation:
    @pytest.mark.anyio
    async def test_dispatch_food_truck_rejects_non_standard_recipe(self, tool_ctx, monkeypatch):
        """Campaign recipe → fleet_invalid_recipe_kind error."""
        from autoskillit.fleet._api import execute_dispatch
        from autoskillit.recipe.schema import Recipe, RecipeKind

        tool_ctx.fleet_lock = FleetSemaphore(max_concurrent=1)
        repo = InMemoryRecipeRepository()
        recipe_info = _make_recipe_info("campaign-recipe")
        repo.add_recipe("campaign-recipe", recipe_info)
        repo.add_full_recipe(
            recipe_info.path,
            Recipe(name="campaign-recipe", description="test", kind=RecipeKind.CAMPAIGN),
        )
        tool_ctx.recipes = repo

        _dispatch_result = await execute_dispatch(
            tool_ctx=tool_ctx,
            recipe="campaign-recipe",
            task="t",
            ingredients=None,
            dispatch_name=None,
            timeout_sec=None,
            prompt_builder=_simple_prompt_builder,
            quota_checker=_no_sleep_quota_checker,
            quota_refresher=_noop_quota_refresher,
        )
        result = json.loads(_dispatch_result.outcome.to_envelope())
        assert result["success"] is False
        assert result["error"] == "fleet_invalid_recipe_kind"

    @pytest.mark.anyio
    async def test_dispatch_food_truck_rejects_unknown_ingredients(self, tool_ctx, monkeypatch):
        """Keys not in recipe.ingredients → fleet_unknown_ingredient error."""
        from autoskillit.fleet._api import execute_dispatch

        tool_ctx.fleet_lock = FleetSemaphore(max_concurrent=1)
        repo = InMemoryRecipeRepository()
        recipe_info = _make_recipe_info("test-recipe")
        repo.add_recipe("test-recipe", recipe_info)
        repo.add_full_recipe(recipe_info.path, _make_standard_recipe("test-recipe", ["task"]))
        tool_ctx.recipes = repo
        tool_ctx.executor = InMemoryHeadlessExecutor()

        _dispatch_result = await execute_dispatch(
            tool_ctx=tool_ctx,
            recipe="test-recipe",
            task="t",
            ingredients={"task": "v", "unknown_key": "bad"},
            dispatch_name=None,
            timeout_sec=None,
            prompt_builder=_simple_prompt_builder,
            quota_checker=_no_sleep_quota_checker,
            quota_refresher=_noop_quota_refresher,
        )
        result = json.loads(_dispatch_result.outcome.to_envelope())
        assert result["success"] is False
        assert result["error"] == "fleet_unknown_ingredient"
        assert "unknown_key" in result["user_visible_message"]

    @pytest.mark.anyio
    async def test_dispatch_food_truck_rejects_non_string_values(self, tool_ctx, monkeypatch):
        """Non-string ingredient values rejected before lock acquisition."""
        from autoskillit.fleet._api import execute_dispatch

        lock = FleetSemaphore(max_concurrent=1)
        tool_ctx.fleet_lock = lock

        _dispatch_result = await execute_dispatch(
            tool_ctx=tool_ctx,
            recipe="r",
            task="t",
            ingredients={"key": 123},  # type: ignore[dict-item]
            dispatch_name=None,
            timeout_sec=None,
            prompt_builder=_simple_prompt_builder,
            quota_checker=_no_sleep_quota_checker,
            quota_refresher=_noop_quota_refresher,
        )
        result = json.loads(_dispatch_result.outcome.to_envelope())
        assert result["success"] is False
        assert result["error"] == "fleet_unknown_ingredient"
        # Lock must not have been acquired
        assert not lock.at_capacity()

    @pytest.mark.anyio
    async def test_dispatch_food_truck_no_recipes_configured(self, tool_ctx, monkeypatch):
        """recipes=None → fleet_manifest_missing error."""
        from autoskillit.fleet._api import execute_dispatch

        tool_ctx.fleet_lock = FleetSemaphore(max_concurrent=1)
        tool_ctx.recipes = None

        _dispatch_result = await execute_dispatch(
            tool_ctx=tool_ctx,
            recipe="r",
            task="t",
            ingredients=None,
            dispatch_name=None,
            timeout_sec=None,
            prompt_builder=_simple_prompt_builder,
            quota_checker=_no_sleep_quota_checker,
            quota_refresher=_noop_quota_refresher,
        )
        result = json.loads(_dispatch_result.outcome.to_envelope())
        assert result["success"] is False
        assert result["error"] == "fleet_manifest_missing"

    @pytest.mark.anyio
    async def test_dispatch_food_truck_no_executor_configured(self, tool_ctx, monkeypatch):
        """executor=None → fleet_manifest_missing error."""
        from autoskillit.fleet._api import execute_dispatch

        tool_ctx.fleet_lock = FleetSemaphore(max_concurrent=1)
        repo = InMemoryRecipeRepository()
        recipe_info = _make_recipe_info("test-recipe")
        repo.add_recipe("test-recipe", recipe_info)
        repo.add_full_recipe(recipe_info.path, _make_standard_recipe("test-recipe"))
        tool_ctx.recipes = repo
        tool_ctx.executor = None

        _dispatch_result = await execute_dispatch(
            tool_ctx=tool_ctx,
            recipe="test-recipe",
            task="t",
            ingredients=None,
            dispatch_name=None,
            timeout_sec=None,
            prompt_builder=_simple_prompt_builder,
            quota_checker=_no_sleep_quota_checker,
            quota_refresher=_noop_quota_refresher,
        )
        result = json.loads(_dispatch_result.outcome.to_envelope())
        assert result["success"] is False
        assert result["error"] == "fleet_manifest_missing"

    @pytest.mark.anyio
    async def test_dispatch_recipe_info_kind_attribute_error_is_fixed(self, tool_ctx):
        """find() returns RecipeInfo in production; dispatch must not crash on .kind.

        Previously all dispatch tests stored Recipe objects in the fake, masking the
        AttributeError. This test uses RecipeInfo (the actual production return type).
        Before fix: recipe_obj.kind raises AttributeError → L3_STARTUP_OR_CRASH.
        After fix: load_recipe upgrades RecipeInfo → Recipe before kind check.
        """
        from autoskillit.fleet._api import execute_dispatch
        from autoskillit.recipe.schema import Recipe, RecipeInfo, RecipeKind, RecipeSource

        tool_ctx.fleet_lock = FleetSemaphore(max_concurrent=1)
        tool_ctx.executor = None
        repo = InMemoryRecipeRepository()
        recipe_info = RecipeInfo(
            name="test-recipe",
            description="test",
            source=RecipeSource.PROJECT,
            path=Path("/fake/recipes/test-recipe.yaml"),
        )
        repo.add_recipe("test-recipe", recipe_info)
        repo.add_full_recipe(
            recipe_info.path,
            Recipe(name="test-recipe", description="test", kind=RecipeKind.STANDARD),
        )
        tool_ctx.recipes = repo

        _dispatch_result = await execute_dispatch(
            tool_ctx=tool_ctx,
            recipe="test-recipe",
            task="run task",
            ingredients=None,
            dispatch_name=None,
            timeout_sec=None,
            prompt_builder=_simple_prompt_builder,
            quota_checker=_no_sleep_quota_checker,
            quota_refresher=_noop_quota_refresher,
        )
        result = json.loads(_dispatch_result.outcome.to_envelope())

        assert result.get("error") != "fleet_l3_startup_or_crash", (
            "Expected structured validation error, not FLEET_L3_STARTUP_OR_CRASH. "
            "RecipeInfo.kind AttributeError is not fixed."
        )

    @pytest.mark.anyio
    async def test_dispatch_recipe_info_ingredients_attribute_error_is_fixed(self, tool_ctx):
        """find() returns RecipeInfo; ingredients validation must not crash on
        recipe_obj.ingredients when non-empty ingredients are passed.

        Before fix: recipe_obj.ingredients raises AttributeError → L3_STARTUP_OR_CRASH.
        After fix: load_recipe upgrades RecipeInfo → Recipe; unknown ingredient detected.
        """
        from autoskillit.fleet._api import execute_dispatch
        from autoskillit.recipe.schema import (
            Recipe,
            RecipeInfo,
            RecipeIngredient,
            RecipeKind,
            RecipeSource,
        )

        tool_ctx.fleet_lock = FleetSemaphore(max_concurrent=1)
        tool_ctx.executor = None
        repo = InMemoryRecipeRepository()
        recipe_info = RecipeInfo(
            name="test-recipe",
            description="test",
            source=RecipeSource.PROJECT,
            path=Path("/fake/recipes/test-recipe.yaml"),
        )
        repo.add_recipe("test-recipe", recipe_info)
        repo.add_full_recipe(
            recipe_info.path,
            Recipe(
                name="test-recipe",
                description="test",
                kind=RecipeKind.STANDARD,
                ingredients={"env": RecipeIngredient(description="env var")},
            ),
        )
        tool_ctx.recipes = repo

        _dispatch_result = await execute_dispatch(
            tool_ctx=tool_ctx,
            recipe="test-recipe",
            task="run task",
            ingredients={"unknown_key": "val"},
            dispatch_name=None,
            timeout_sec=None,
            prompt_builder=_simple_prompt_builder,
            quota_checker=_no_sleep_quota_checker,
            quota_refresher=_noop_quota_refresher,
        )
        result = json.loads(_dispatch_result.outcome.to_envelope())

        assert result.get("error") == "fleet_unknown_ingredient", (
            f"Expected fleet_unknown_ingredient error. Got: {result}"
        )


class TestDispatchFoodTruckSemanticValidation:
    @pytest.mark.anyio
    async def test_dispatch_rejects_recipe_with_semantic_validation_errors(
        self, tool_ctx, monkeypatch
    ):
        """load_and_validate returning valid=False → fleet_recipe_invalid error.

        The dispatch path now calls load_and_validate() before dispatch.
        When validation finds ERROR-severity findings, dispatch is rejected
        with FLEET_RECIPE_INVALID rather than proceeding to execution.
        """
        from autoskillit.fleet._api import execute_dispatch

        tool_ctx.fleet_lock = FleetSemaphore(max_concurrent=1)
        repo = InMemoryRecipeRepository()
        recipe_info = _make_recipe_info("test-recipe")
        repo.add_recipe("test-recipe", recipe_info)
        repo.add_full_recipe(recipe_info.path, _make_standard_recipe("test-recipe", ["task"]))
        repo.set_validated(
            "test-recipe",
            {
                "valid": False,
                "suggestions": [
                    {
                        "rule": "run-cmd-script-exists",
                        "severity": "error",
                        "step_name": "step_a",
                        "message": (
                            "Step 'step_a' runs bash /nonexistent/script.sh"
                            " but the script does not exist"
                        ),
                    }
                ],
            },
        )
        tool_ctx.recipes = repo
        tool_ctx.executor = InMemoryHeadlessExecutor()

        _dispatch_result = await execute_dispatch(
            tool_ctx=tool_ctx,
            recipe="test-recipe",
            task="t",
            ingredients=None,
            dispatch_name=None,
            timeout_sec=None,
            prompt_builder=_simple_prompt_builder,
            quota_checker=_no_sleep_quota_checker,
            quota_refresher=_noop_quota_refresher,
        )
        result = json.loads(_dispatch_result.outcome.to_envelope())
        assert result["success"] is False
        assert result["error"] == "fleet_recipe_invalid"
        assert "run-cmd-script-exists" in result["user_visible_message"]
        assert "step_a" in result["user_visible_message"]

    @pytest.mark.anyio
    async def test_dispatch_accepts_recipe_with_valid_semantic_validation(self, tool_ctx):
        """load_and_validate returning valid=True → dispatch proceeds normally.

        When a recipe passes semantic validation, dispatch proceeds through
        the normal execution path (kind check, ingredient check, etc.).
        """
        from autoskillit.fleet._api import execute_dispatch
        from tests.fakes import _DEFAULT_SKILL_RESULT

        tool_ctx.fleet_lock = FleetSemaphore(max_concurrent=1)
        repo = InMemoryRecipeRepository()
        recipe_info = _make_recipe_info("test-recipe")
        repo.add_recipe("test-recipe", recipe_info)
        repo.add_full_recipe(recipe_info.path, _make_standard_recipe("test-recipe", ["task"]))
        repo.set_validated(
            "test-recipe",
            {"valid": True, "suggestions": []},
        )
        tool_ctx.recipes = repo
        tool_ctx.executor = InMemoryHeadlessExecutor(default_result=_DEFAULT_SKILL_RESULT)

        raw = await execute_dispatch(
            tool_ctx=tool_ctx,
            recipe="test-recipe",
            task="t",
            ingredients=None,
            dispatch_name=None,
            timeout_sec=None,
            prompt_builder=_simple_prompt_builder,
            quota_checker=_no_sleep_quota_checker,
            quota_refresher=_noop_quota_refresher,
        )
        result = json.loads(raw.outcome.to_envelope())
        assert result.get("error") != "fleet_recipe_invalid", f"Got: {result}"
        assert "dispatch_id" in result

    @pytest.mark.anyio
    async def test_dispatch_rejects_when_load_and_validate_raises(self, tool_ctx, monkeypatch):
        """load_and_validate raising an exception → fleet_recipe_invalid error."""
        from autoskillit.fleet._api import execute_dispatch

        tool_ctx.fleet_lock = FleetSemaphore(max_concurrent=1)

        class RaisingRepo(InMemoryRecipeRepository):
            def load_and_validate(self, *args, **kwargs):
                raise RuntimeError("validation infrastructure broken")

        repo = RaisingRepo()
        recipe_info = _make_recipe_info("test-recipe")
        repo.add_recipe("test-recipe", recipe_info)
        tool_ctx.recipes = repo

        _dispatch_result = await execute_dispatch(
            tool_ctx=tool_ctx,
            recipe="test-recipe",
            task="t",
            ingredients=None,
            dispatch_name=None,
            timeout_sec=None,
            prompt_builder=_simple_prompt_builder,
            quota_checker=_no_sleep_quota_checker,
            quota_refresher=_noop_quota_refresher,
        )
        result = json.loads(_dispatch_result.outcome.to_envelope())
        assert result["success"] is False
        assert result["error"] == "fleet_recipe_invalid"
        assert "could not be loaded" in result["user_visible_message"]
        assert "validation infrastructure broken" in result["user_visible_message"]
