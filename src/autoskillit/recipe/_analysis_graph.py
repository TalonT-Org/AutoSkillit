"""Backward-compat shim for ``recipe/_analysis_graph.py``.

Real implementation: ``autoskillit.recipe.analysis._analysis_graph`` (#4671 D).
Preserves old import path ``autoskillit.recipe._analysis_graph``.
"""

from __future__ import annotations

from autoskillit.recipe.analysis._analysis_graph import (
    RECIPE_TERMINAL_TARGETS,
    TYPE_CHECKING,
    Recipe,
    RecipeStep,
    RouteEdge,
    _build_raw_step_edges,
    _build_step_graph,
    _extract_routing_edges,
    _is_infrastructure_step,
    annotations,
    build_recipe_graph,
    get_logger,
    logger,
)

__all__ = [
    "RECIPE_TERMINAL_TARGETS",
    "Recipe",
    "RecipeStep",
    "RouteEdge",
    "TYPE_CHECKING",
    "_build_raw_step_edges",
    "_build_step_graph",
    "_extract_routing_edges",
    "_is_infrastructure_step",
    "annotations",
    "build_recipe_graph",
    "get_logger",
    "logger",
]
