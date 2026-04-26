"""Tests for campaign capture extraction and ingredient interpolation (Group J)."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

pytestmark = [pytest.mark.layer("fleet"), pytest.mark.small, pytest.mark.feature("fleet")]


# ---------------------------------------------------------------------------
# Capture extraction tests
# ---------------------------------------------------------------------------


def test_extract_captures_from_payload():
    from autoskillit.fleet._api import _extract_captures

    result = _extract_captures(
        {"sources_manifest": "${{ result.sources_manifest }}"},
        {"sources_manifest": "/tmp/sources.json", "extra": "ignored"},
    )
    assert result == {"sources_manifest": "/tmp/sources.json"}


def test_extract_captures_missing_field_skipped():
    from autoskillit.fleet._api import _extract_captures

    result = _extract_captures(
        {"missing_key": "${{ result.missing_key }}"},
        {"other": "value"},
    )
    assert "missing_key" not in result


def test_extract_captures_non_result_template_skipped():
    from autoskillit.fleet._api import _extract_captures

    result = _extract_captures(
        {"a": "plain_string", "b": "${{ inputs.x }}"},
        {"plain_string": "val", "x": "val2"},
    )
    assert result == {}


def test_extract_captures_converts_value_to_str():
    from autoskillit.fleet._api import _extract_captures

    result = _extract_captures(
        {"count": "${{ result.count }}"},
        {"count": 42},
    )
    assert result == {"count": "42"}


# ---------------------------------------------------------------------------
# Ingredient interpolation tests
# ---------------------------------------------------------------------------


def test_interpolate_campaign_refs_basic():
    from autoskillit.fleet._api import _interpolate_campaign_refs

    result = _interpolate_campaign_refs({"k": "${{ campaign.v }}"}, {"v": "resolved"})
    assert result == {"k": "resolved"}


def test_interpolate_unresolved_ref_raises_value_error():
    from autoskillit.fleet._api import _interpolate_campaign_refs

    with pytest.raises(ValueError, match="missing"):
        _interpolate_campaign_refs({"k": "${{ campaign.missing }}"}, {})


def test_interpolate_passthrough_non_campaign_values():
    from autoskillit.fleet._api import _interpolate_campaign_refs

    result = _interpolate_campaign_refs(
        {"a": "${{ inputs.x }}", "b": "plain"},
        {},
    )
    assert result == {"a": "${{ inputs.x }}", "b": "plain"}


def test_interpolate_multiple_refs_in_one_value():
    from autoskillit.fleet._api import _interpolate_campaign_refs

    result = _interpolate_campaign_refs(
        {"path": "${{ campaign.a }}/${{ campaign.b }}"},
        {"a": "foo", "b": "bar"},
    )
    assert result == {"path": "foo/bar"}


# ---------------------------------------------------------------------------
# Integration path via execute_dispatch
# ---------------------------------------------------------------------------


def _make_recipe_info(name: str = "test-recipe"):
    from autoskillit.recipe.schema import RecipeInfo, RecipeSource

    return RecipeInfo(
        name=name,
        description="test",
        source=RecipeSource.PROJECT,
        path=Path(f"/fake/{name}.yaml"),
    )


def _simple_prompt_builder(**kwargs) -> str:
    return f"prompt-for-{kwargs.get('recipe', 'unknown')}"


async def _no_sleep_quota_checker(config, **kwargs) -> dict:
    return {
        "should_sleep": False,
        "sleep_seconds": 0,
        "utilization": None,
        "resets_at": None,
        "window_name": None,
    }


async def _noop_quota_refresher(config, **kwargs) -> None:
    pass


def _setup_dispatch(tool_ctx, recipe_name: str = "test-recipe", ingredients: dict | None = None):
    from autoskillit.recipe.schema import Recipe, RecipeKind
    from tests.fakes import InMemoryHeadlessExecutor, InMemoryRecipeRepository

    tool_ctx.fleet_lock = asyncio.Lock()
    repo = InMemoryRecipeRepository()
    recipe_info = _make_recipe_info(recipe_name)
    repo.add_recipe(recipe_name, recipe_info)
    repo.add_full_recipe(
        recipe_info.path,
        Recipe(
            name=recipe_name,
            description="test",
            kind=RecipeKind.STANDARD,
            ingredients=ingredients or {},
        ),
    )
    tool_ctx.recipes = repo
    tool_ctx.executor = InMemoryHeadlessExecutor()


def _make_success_result(payload: dict):

    from autoskillit.core.types import SkillResult

    body = json.dumps(payload)
    sentinel_id_placeholder = "PLACEHOLDER"
    stdout = (
        f"%%L2_DONE::{sentinel_id_placeholder}%%\n---l2-result---\n{body}\n---end-l2-result---"
    )
    return SkillResult(
        success=True,
        result=stdout,
        session_id="sess-123",
        subtype="success",
        is_error=False,
        exit_code=0,
        needs_retry=False,
        retry_reason="none",
        stderr="",
        token_usage=None,
    )


def _read_state_file(tool_ctx) -> dict:
    state_files = list((tool_ctx.temp_dir / "dispatches").glob("*.json"))
    return json.loads(state_files[0].read_text())


@pytest.mark.anyio
async def test_dispatch_captures_extracted_and_written_to_state(tool_ctx, monkeypatch):
    """After a successful dispatch with capture spec, state file has captured_values."""

    from autoskillit.fleet._api import execute_dispatch

    _setup_dispatch(tool_ctx)

    payload = {"success": True, "reason": "", "out": "hello"}

    # The actual dispatch ID isn't known ahead of time; we patch parse_l2_result_block
    # to return a clean result with the payload.
    from autoskillit.fleet.result_parser import L2ParseResult

    monkeypatch.setattr(
        "autoskillit.fleet._api.parse_l2_result_block",
        lambda **kwargs: L2ParseResult(
            outcome="completed_clean",
            payload=payload,
            raw_body=None,
            parse_error=None,
            source="stdout",
        ),
    )

    raw = await execute_dispatch(
        tool_ctx=tool_ctx,
        recipe="test-recipe",
        task="t",
        ingredients=None,
        dispatch_name=None,
        timeout_sec=None,
        capture={"out": "${{ result.out }}"},
        prompt_builder=_simple_prompt_builder,
        quota_checker=_no_sleep_quota_checker,
        quota_refresher=_noop_quota_refresher,
    )

    result = json.loads(raw)
    assert result["success"] is True

    dispatch_files = list((tool_ctx.temp_dir / "dispatches").glob("*.json"))
    assert len(dispatch_files) == 1, f"Expected 1 state file, found {len(dispatch_files)}"
    state_data = _read_state_file(tool_ctx)
    assert state_data.get("captured_values") == {"out": "hello"}


@pytest.mark.anyio
async def test_dispatch_ingredients_interpolated_from_captured_values(tool_ctx, monkeypatch):
    """Prior captured_values in state file are resolved into ingredients before dispatch."""

    from autoskillit.fleet._api import execute_dispatch
    from autoskillit.fleet.result_parser import L2ParseResult
    from autoskillit.fleet.state import DispatchRecord, write_captured_values, write_initial_state

    # Pre-create a state file for the same campaign_id with captured_values
    campaign_id = tool_ctx.kitchen_id
    dispatches_dir = tool_ctx.temp_dir / "dispatches"
    dispatches_dir.mkdir(parents=True, exist_ok=True)
    prior_state_path = dispatches_dir / "prior.json"
    write_initial_state(
        prior_state_path,
        campaign_id=campaign_id,
        campaign_name="prior-dispatch",
        manifest_path="",
        dispatches=[DispatchRecord(name="prior-dispatch")],
    )
    from autoskillit.fleet.state import DispatchStatus, append_dispatch_record

    append_dispatch_record(
        prior_state_path,
        DispatchRecord(name="prior-dispatch", status=DispatchStatus.SUCCESS),
    )
    write_captured_values(prior_state_path, {"v": "injected"})

    _setup_dispatch(tool_ctx, ingredients={"x": ""})

    received_ingredients: list[dict] = []

    def _capturing_prompt_builder(**kwargs):
        received_ingredients.append(kwargs.get("ingredients", {}))
        return "prompt"

    monkeypatch.setattr(
        "autoskillit.fleet._api.parse_l2_result_block",
        lambda **kwargs: L2ParseResult(
            outcome="completed_clean",
            payload={"success": True, "reason": ""},
            raw_body=None,
            parse_error=None,
            source="stdout",
        ),
    )

    await execute_dispatch(
        tool_ctx=tool_ctx,
        recipe="test-recipe",
        task="t",
        ingredients={"x": "${{ campaign.v }}"},
        dispatch_name=None,
        timeout_sec=None,
        capture=None,
        prompt_builder=_capturing_prompt_builder,
        quota_checker=_no_sleep_quota_checker,
        quota_refresher=_noop_quota_refresher,
    )

    assert received_ingredients, "prompt_builder was not called"
    assert len(received_ingredients) == 1
    assert received_ingredients[0].get("x") == "injected"


@pytest.mark.anyio
async def test_unresolved_campaign_ref_in_ingredients_returns_fleet_error(tool_ctx, monkeypatch):
    """Dispatch with ${{ campaign.missing }} and no prior captures returns fleet_error."""
    from autoskillit.fleet._api import execute_dispatch

    _setup_dispatch(tool_ctx, ingredients={"x": ""})

    raw = await execute_dispatch(
        tool_ctx=tool_ctx,
        recipe="test-recipe",
        task="t",
        ingredients={"x": "${{ campaign.missing }}"},
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
