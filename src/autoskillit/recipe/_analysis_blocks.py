"""Backward-compat shim for ``recipe/_analysis_blocks.py``.

Real implementation: ``autoskillit.recipe.analysis._analysis_blocks`` (#4671 D).
Preserves old import path ``autoskillit.recipe._analysis_blocks``.
"""

from __future__ import annotations

from autoskillit.recipe.analysis._analysis_blocks import (
    Recipe,
    RecipeBlock,
    RecipeStep,
    _count_by_tool,
    _count_gh_api,
    annotations,
    extract_blocks,
    get_logger,
    logger,
)

__all__ = [
    "Recipe",
    "RecipeBlock",
    "RecipeStep",
    "_count_by_tool",
    "_count_gh_api",
    "annotations",
    "extract_blocks",
    "get_logger",
    "logger",
]
