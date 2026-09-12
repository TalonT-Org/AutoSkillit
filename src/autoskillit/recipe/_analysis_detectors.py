"""Backward-compat shim for ``recipe/_analysis_detectors.py``.

Real implementation: ``autoskillit.recipe.analysis._analysis_detectors`` (#4671 D).
Preserves old import path ``autoskillit.recipe._analysis_detectors``.
"""

from __future__ import annotations

from autoskillit.recipe.analysis._analysis_detectors import (
    _CONTEXT_REF_RE,
    _INVALIDATING_TOOLS,
    _OBSERVABILITY_CAPTURES,
    SKILL_TOOLS,
    DataFlowWarning,
    Recipe,
    RecipeStep,
    _bfs_capped,
    _build_capture_origin_map,
    _context_refs_in_value,
    _detect_dead_outputs,
    _detect_implicit_handoffs,
    _detect_ref_invalidations,
    _detect_stale_captured_paths,
    _is_observability_capture,
    annotations,
    bfs_reachable,
)

__all__ = [
    "DataFlowWarning",
    "Recipe",
    "RecipeStep",
    "SKILL_TOOLS",
    "_CONTEXT_REF_RE",
    "_INVALIDATING_TOOLS",
    "_OBSERVABILITY_CAPTURES",
    "_bfs_capped",
    "_build_capture_origin_map",
    "_context_refs_in_value",
    "_detect_dead_outputs",
    "_detect_implicit_handoffs",
    "_detect_ref_invalidations",
    "_detect_stale_captured_paths",
    "_is_observability_capture",
    "annotations",
    "bfs_reachable",
]
