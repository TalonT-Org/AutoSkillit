"""Backward-compat shim for ``recipe/_recipe_raw_repair.py``.

Real implementation: ``autoskillit.recipe.ingredients._recipe_raw_repair`` (#4671 D).
Preserves old import path ``autoskillit.recipe._recipe_raw_repair``.
"""

from __future__ import annotations

from autoskillit.recipe.ingredients._recipe_raw_repair import (
    Any,
    RecipeStep,
    _resolve_skip_guards_in_content,
    _resolve_skip_redirects,
    annotations,
    compose_yaml,
    is_yaml_mapping_node,
)

__all__ = [
    "Any",
    "RecipeStep",
    "_resolve_skip_guards_in_content",
    "_resolve_skip_redirects",
    "annotations",
    "compose_yaml",
    "is_yaml_mapping_node",
]
