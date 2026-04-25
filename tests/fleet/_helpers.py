"""Shared helpers for tests/franchise/ test modules."""

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
