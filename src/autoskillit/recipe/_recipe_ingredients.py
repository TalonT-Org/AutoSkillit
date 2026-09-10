"""Backward-compat shim for ``recipe/_recipe_ingredients.py``.

Real implementation: ``autoskillit.recipe.ingredients._recipe_ingredients`` (#4671 D).
Preserves old import path ``autoskillit.recipe._recipe_ingredients``.
"""

from __future__ import annotations

from autoskillit.recipe.ingredients._recipe_ingredients import (
    _GFM_DESC_MAX_WIDTH,
    _GFM_INGREDIENT_COLUMNS,
    CALLER_SOVEREIGN_INGREDIENTS,
    Any,
    DeferredGuard,
    FinalizedRecipeProjection,
    ListRecipesResult,
    LoadRecipeResult,
    NotRequired,
    OpenKitchenResult,
    RecipeListItem,
    TerminalColumn,
    TypedDict,
    _ingredient_sort_key,
    _render_gfm_table,
    annotations,
    build_ingredient_rows,
    format_ingredients_table,
)

__all__ = [
    "Any",
    "CALLER_SOVEREIGN_INGREDIENTS",
    "DeferredGuard",
    "FinalizedRecipeProjection",
    "ListRecipesResult",
    "LoadRecipeResult",
    "NotRequired",
    "OpenKitchenResult",
    "RecipeListItem",
    "TerminalColumn",
    "TypedDict",
    "_GFM_DESC_MAX_WIDTH",
    "_GFM_INGREDIENT_COLUMNS",
    "_ingredient_sort_key",
    "_render_gfm_table",
    "annotations",
    "build_ingredient_rows",
    "format_ingredients_table",
]
