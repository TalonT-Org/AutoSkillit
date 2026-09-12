"""Backward-compat shim for ``recipe/_analysis_bfs.py``.

Real implementation: ``autoskillit.recipe.analysis._analysis_bfs`` (#4671 D).
Preserves old import path ``autoskillit.recipe._analysis_bfs``.
"""

from __future__ import annotations

from autoskillit.recipe.analysis._analysis_bfs import (
    _INVALIDATING_TOOLS,
    _bfs_capped,
    _bfs_with_facts,
    _build_capture_origin_map,
    _build_step_graph,
    _build_success_step_graph,
    all_paths_cross,
    bfs_reachable,
    bfs_reachable_without_barrier,
    bfs_reachable_without_barrier_in_graph,
)

__all__ = [
    "bfs_reachable",
    "bfs_reachable_without_barrier",
    "bfs_reachable_without_barrier_in_graph",
    "all_paths_cross",
    "_build_step_graph",
    "_build_success_step_graph",
    "_bfs_capped",
    "_bfs_with_facts",
    "_build_capture_origin_map",
    "_INVALIDATING_TOOLS",
]
