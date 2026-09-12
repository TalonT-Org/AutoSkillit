"""Backward-compat shim for ``recipe/_api_orchestration_assemble.py``.

Real implementation: ``autoskillit.recipe.api_orchestration._api_orchestration_assemble`` (#4951).
Preserves old import path ``autoskillit.recipe._api_orchestration_assemble``.
"""

from __future__ import annotations

from autoskillit.recipe.api_orchestration._api_orchestration_assemble import (  # noqa: F401
    Any,
    DeferredGuard,
    FinalizedRecipeStep,
    LoadRecipeResult,
    Recipe,
    _assemble_load_result,
    _finalize_recipe_steps,
    annotate_diagram_with_pruning,
    annotations,
    assert_no_raw_placeholders,
    cast,
    format_ingredients_table,
    load_recipe_diagram,
)

__all__ = ["_assemble_load_result", "_finalize_recipe_steps"]
