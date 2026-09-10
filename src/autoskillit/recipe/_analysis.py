"""Backward-compat shim for ``recipe/_analysis.py``.

Real implementation: ``autoskillit.recipe.analysis._analysis`` (#4671 D).
Preserves old import path ``autoskillit.recipe._analysis``.
"""

from __future__ import annotations

from autoskillit.recipe.analysis._analysis import (
    RouteEdge,
    ValidationContext,
    _bfs_with_facts,
    _build_raw_step_edges,
    _build_step_graph,
    _detect_dead_outputs,
    _detect_implicit_handoffs,
    _detect_ref_invalidations,
    _extract_routing_edges,
    _is_infrastructure_step,
    analyze_dataflow,
    bfs_reachable,
    build_recipe_graph,
    extract_blocks,
    iter_steps_with_context,
    make_validation_context,
)

__all__ = [
    "build_recipe_graph",
    "RouteEdge",
    "_extract_routing_edges",
    "_build_step_graph",
    "_build_raw_step_edges",
    "_is_infrastructure_step",
    "bfs_reachable",
    "_bfs_with_facts",
    "extract_blocks",
    "_detect_dead_outputs",
    "_detect_ref_invalidations",
    "_detect_implicit_handoffs",
    "ValidationContext",
    "analyze_dataflow",
    "make_validation_context",
    "iter_steps_with_context",
]
