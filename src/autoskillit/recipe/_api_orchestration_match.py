"""Backward-compat shim for ``recipe/_api_orchestration_match.py``.

Real implementation: ``autoskillit.recipe.api_orchestration._api_orchestration_match`` (#4951).
Preserves old import path ``autoskillit.recipe._api_orchestration_match``.
"""

from __future__ import annotations

from autoskillit.recipe.api_orchestration._api_orchestration_match import (  # noqa: F401
    RecipeInfo,
    RecipeNotFoundError,
    RecipeSource,
    _resolve_recipe_match,
    annotations,
    find_recipe_by_name,
    substitute_scripts_placeholder,
    substitute_temp_placeholder,
)

__all__ = ["_resolve_recipe_match"]
