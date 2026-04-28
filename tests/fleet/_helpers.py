"""Shared helpers for tests/fleet/ test modules."""

from __future__ import annotations

from autoskillit.core._type_constants import PACK_REGISTRY, TOOL_SUBSET_TAGS

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


def _make_recipe_info(name: str = "test-recipe", path_prefix: str = "/fake/"):
    from pathlib import Path

    from autoskillit.recipe.schema import RecipeInfo, RecipeSource

    return RecipeInfo(
        name=name,
        description="test",
        source=RecipeSource.PROJECT,
        path=Path(f"{path_prefix}{name}.yaml"),
    )


def _setup_dispatch(tool_ctx, monkeypatch, recipe_name: str = "test-recipe"):
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
        Recipe(name=recipe_name, description="test", kind=RecipeKind.STANDARD, ingredients={}),
    )
    tool_ctx.recipes = repo
    tool_ctx.executor = InMemoryHeadlessExecutor()
