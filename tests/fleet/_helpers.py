"""Shared helpers for tests/fleet/ test modules."""

from __future__ import annotations

import json

from autoskillit.core.types._type_constants import PACK_REGISTRY, TOOL_SUBSET_TAGS

# ---------------------------------------------------------------------------
# Module-level constants — derived from the authoritative TOOL_SUBSET_TAGS map
# ---------------------------------------------------------------------------

_tools_by_pack: dict[str, set[str]] = {}
for _tool, _tags in TOOL_SUBSET_TAGS.items():
    for _tag in _tags:
        if _tag in PACK_REGISTRY:
            _tools_by_pack.setdefault(_tag, set()).add(_tool)

TOOLS_BY_PACK: dict[str, frozenset[str]] = {k: frozenset(v) for k, v in _tools_by_pack.items()}

KITCHEN_CORE_TOOLS = TOOLS_BY_PACK["kitchen-core"]


def compute_food_truck_tool_surface(recipe_name: str) -> frozenset[str]:
    """Compute the expected tool surface for a food truck running the given recipe."""
    from autoskillit.recipe.io import builtin_recipes_dir, load_recipe

    path = builtin_recipes_dir() / f"{recipe_name}.yaml"
    recipe = load_recipe(path)
    expected: set[str] = set(KITCHEN_CORE_TOOLS)
    for pack in recipe.requires_packs or []:
        expected |= TOOLS_BY_PACK.get(pack, frozenset())
    return frozenset(expected)


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


def _make_recipe_info(name: str = "test-recipe", path_prefix: str = "/fake/"):
    from pathlib import Path

    from autoskillit.recipe.schema import RecipeInfo, RecipeSource

    return RecipeInfo(
        name=name,
        description="test",
        source=RecipeSource.PROJECT,
        path=Path(f"{path_prefix}{name}.yaml"),
    )


def _setup_dispatch(
    tool_ctx,
    monkeypatch,
    recipe_name: str = "test-recipe",
    *,
    requires_packs: list[str] | None = None,
):
    """Wire tool_ctx for dispatch tests."""
    from autoskillit.fleet import FleetSemaphore
    from autoskillit.recipe.schema import Recipe, RecipeKind
    from tests.fakes import InMemoryHeadlessExecutor, InMemoryRecipeRepository

    tool_ctx.fleet_lock = FleetSemaphore(max_concurrent=1)
    repo = InMemoryRecipeRepository()
    recipe_info = _make_recipe_info(recipe_name)
    repo.add_recipe(recipe_name, recipe_info)
    repo.add_full_recipe(
        recipe_info.path,
        Recipe(
            name=recipe_name,
            description="test",
            kind=RecipeKind.STANDARD,
            ingredients={},
            requires_packs=requires_packs if requires_packs is not None else [],
        ),
    )
    tool_ctx.recipes = repo
    tool_ctx.executor = InMemoryHeadlessExecutor()


async def _run(
    tool_ctx, recipe: str = "test-recipe", ingredients: dict[str, str] | None = None
) -> dict:
    from autoskillit.fleet._api import execute_dispatch

    result = await execute_dispatch(
        tool_ctx=tool_ctx,
        recipe=recipe,
        task="t",
        ingredients=ingredients,
        dispatch_name=None,
        timeout_sec=None,
        prompt_builder=_simple_prompt_builder,
        quota_checker=_no_sleep_quota_checker,
        quota_refresher=_noop_quota_refresher,
    )
    return json.loads(result.outcome.to_envelope())


def _read_dispatch_record(tool_ctx) -> dict:
    """Read the single dispatch record written to the state file."""
    state_files = list((tool_ctx.temp_dir / "dispatches").glob("*.json"))
    assert len(state_files) == 1, f"Expected 1 state file, found {len(state_files)}"
    state = json.loads(state_files[0].read_text())
    return state["dispatches"][0]


def _make_no_sentinel():
    from autoskillit.fleet.result_parser import L3ParseResult

    return L3ParseResult(
        outcome="no_sentinel",
        payload=None,
        raw_body=None,
        parse_error=None,
        source="stdout",
    )


def _make_completed_dirty():
    from autoskillit.fleet.result_parser import L3ParseResult

    return L3ParseResult(
        outcome="completed_dirty",
        payload=None,
        raw_body="garbled",
        parse_error="json decode error",
        source="stdout",
    )


def _make_completed_clean(success: bool, reason: str = ""):
    from autoskillit.fleet.result_parser import L3ParseResult

    payload: dict = {"success": success}
    if reason:
        payload["reason"] = reason
    return L3ParseResult(
        outcome="completed_clean",
        payload=payload,
        raw_body=None,
        parse_error=None,
        source="stdout",
    )
