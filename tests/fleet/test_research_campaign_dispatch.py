"""Tests for multi-recipe research campaign dispatch capture propagation (Group J)."""

from __future__ import annotations

import json

import pytest

from tests.fleet._helpers import (
    _make_recipe_info,
    _no_sleep_quota_checker,
    _noop_quota_refresher,
    _simple_prompt_builder,
)

pytestmark = [pytest.mark.layer("fleet"), pytest.mark.small, pytest.mark.feature("fleet")]


def _read_state_file(tool_ctx) -> dict:
    state_files = list((tool_ctx.temp_dir / "dispatches").glob("*.json"))
    assert len(state_files) == 1, f"Expected 1 state file, found {len(state_files)}: {state_files}"
    return json.loads(state_files[0].read_text())


def _setup_research_campaign(tool_ctx):
    from autoskillit.fleet import FleetSemaphore
    from autoskillit.recipe.schema import Recipe, RecipeIngredient, RecipeKind
    from tests.fakes import InMemoryHeadlessExecutor, InMemoryRecipeRepository

    tool_ctx.fleet_lock = FleetSemaphore(max_concurrent=1)
    repo = InMemoryRecipeRepository()

    design_info = _make_recipe_info("research-design")
    repo.add_recipe("research-design", design_info)
    repo.add_full_recipe(
        design_info.path,
        Recipe(
            name="research-design",
            description="Research design phase",
            kind=RecipeKind.STANDARD,
            ingredients={},
        ),
    )

    impl_info = _make_recipe_info("research-implement")
    repo.add_recipe("research-implement", impl_info)
    repo.add_full_recipe(
        impl_info.path,
        Recipe(
            name="research-implement",
            description="Research implement phase",
            kind=RecipeKind.STANDARD,
            ingredients={
                "worktree_path": RecipeIngredient(description="Path to worktree"),
                "research_dir": RecipeIngredient(description="Path to research dir"),
            },
        ),
    )

    tool_ctx.recipes = repo
    tool_ctx.executor = InMemoryHeadlessExecutor()


@pytest.mark.anyio
async def test_design_captured_values_propagate_to_implement_dispatch(tool_ctx, monkeypatch):
    """End-to-end: design dispatch captures propagate to implement dispatch via state file."""
    from autoskillit.fleet._api import execute_dispatch
    from autoskillit.fleet.result_parser import L3ParseResult

    _setup_research_campaign(tool_ctx)

    monkeypatch.setattr(
        "autoskillit.fleet._api.parse_l3_result_block",
        lambda **_: L3ParseResult(
            outcome="completed_clean",
            payload={
                "success": True,
                "worktree_path": "/tmp/wt/proj",
                "research_dir": "/tmp/wt/proj/.research",
            },
            raw_body=None,
            parse_error=None,
            source="stdout",
        ),
    )

    raw = await execute_dispatch(
        tool_ctx=tool_ctx,
        recipe="research-design",
        task="Design the research",
        ingredients=None,
        dispatch_name=None,
        timeout_sec=None,
        capture={
            "worktree_path": "${{ result.worktree_path }}",
            "research_dir": "${{ result.research_dir }}",
        },
        prompt_builder=_simple_prompt_builder,
        quota_checker=_no_sleep_quota_checker,
        quota_refresher=_noop_quota_refresher,
    )

    result = json.loads(raw)
    assert result["success"] is True

    state_data = _read_state_file(tool_ctx)
    assert state_data.get("captured_values") == {
        "worktree_path": "/tmp/wt/proj",
        "research_dir": "/tmp/wt/proj/.research",
    }

    received_ingredients: list[dict] = []

    def _capturing_prompt_builder(**kwargs):
        received_ingredients.append(kwargs.get("ingredients", {}))
        return "prompt"

    monkeypatch.setattr(
        "autoskillit.fleet._api.parse_l3_result_block",
        lambda **kwargs: L3ParseResult(
            outcome="completed_clean",
            payload={"success": True},
            raw_body=None,
            parse_error=None,
            source="stdout",
        ),
    )

    raw = await execute_dispatch(
        tool_ctx=tool_ctx,
        recipe="research-implement",
        task="Implement the research",
        ingredients={
            "worktree_path": "${{ campaign.worktree_path }}",
            "research_dir": "${{ campaign.research_dir }}",
        },
        dispatch_name=None,
        timeout_sec=None,
        capture=None,
        prompt_builder=_capturing_prompt_builder,
        quota_checker=_no_sleep_quota_checker,
        quota_refresher=_noop_quota_refresher,
    )

    assert received_ingredients, "prompt_builder was not called"
    assert received_ingredients[0] == {
        "worktree_path": "/tmp/wt/proj",
        "research_dir": "/tmp/wt/proj/.research",
    }


@pytest.mark.anyio
async def test_missing_campaign_ref_returns_fleet_error(tool_ctx):
    """Dispatch with ${{ campaign.worktree_path }} and no prior captures returns fleet_error."""
    from autoskillit.fleet._api import execute_dispatch

    _setup_research_campaign(tool_ctx)

    raw = await execute_dispatch(
        tool_ctx=tool_ctx,
        recipe="research-implement",
        task="Implement with missing ref",
        ingredients={"worktree_path": "${{ campaign.worktree_path }}"},
        dispatch_name=None,
        timeout_sec=None,
        capture=None,
        prompt_builder=_simple_prompt_builder,
        quota_checker=_no_sleep_quota_checker,
        quota_refresher=_noop_quota_refresher,
    )

    result = json.loads(raw)
    assert result["success"] is False
    assert result["error"] == "fleet_unknown_ingredient"
    assert "campaign.worktree_path" in result["user_visible_message"]


@pytest.mark.anyio
async def test_partial_capture_propagates_only_captured_keys(tool_ctx, monkeypatch):
    """Design captured only worktree_path; implement requests uncaptured research_dir."""
    from autoskillit.fleet._api import execute_dispatch
    from autoskillit.fleet.result_parser import L3ParseResult

    _setup_research_campaign(tool_ctx)

    monkeypatch.setattr(
        "autoskillit.fleet._api.parse_l3_result_block",
        lambda **kwargs: L3ParseResult(
            outcome="completed_clean",
            payload={"success": True, "worktree_path": "/tmp/wt/proj"},
            raw_body=None,
            parse_error=None,
            source="stdout",
        ),
    )

    raw = await execute_dispatch(
        tool_ctx=tool_ctx,
        recipe="research-design",
        task="Design with partial capture",
        ingredients=None,
        dispatch_name=None,
        timeout_sec=None,
        capture={"worktree_path": "${{ result.worktree_path }}"},
        prompt_builder=_simple_prompt_builder,
        quota_checker=_no_sleep_quota_checker,
        quota_refresher=_noop_quota_refresher,
    )

    result = json.loads(raw)
    assert result["success"] is True

    raw = await execute_dispatch(
        tool_ctx=tool_ctx,
        recipe="research-implement",
        task="Implement with missing research_dir",
        ingredients={
            "worktree_path": "${{ campaign.worktree_path }}",
            "research_dir": "${{ campaign.research_dir }}",
        },
        dispatch_name=None,
        timeout_sec=None,
        capture=None,
        prompt_builder=_simple_prompt_builder,
        quota_checker=_no_sleep_quota_checker,
        quota_refresher=_noop_quota_refresher,
    )

    result = json.loads(raw)
    assert result["success"] is False
    assert "error" in result
    assert "campaign.research_dir" in result["user_visible_message"]
