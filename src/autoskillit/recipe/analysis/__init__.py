"""IL-2 recipe analysis primitives — BFS/graph/blocks/detectors (#4671 Phase D).

Sub-package of :mod:`autoskillit.recipe`. Real implementations live in this
sub-package; backward-compat shims at the old ``recipe/_analysis*.py`` paths
preserve existing import sites.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ._analysis import (
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
    )  # noqa: F401
    from ._analysis_bfs import (
        _INVALIDATING_TOOLS,
        _bfs_capped,
        _build_capture_origin_map,
        _build_success_step_graph,
        all_paths_cross,
        bfs_reachable_without_barrier,
        bfs_reachable_without_barrier_in_graph,
    )  # noqa: F401
    from ._analysis_blocks import (  # noqa: F401
        Recipe,
        RecipeBlock,
        RecipeStep,
        _count_by_tool,
        _count_gh_api,
        get_logger,
        logger,
    )
    from ._analysis_detectors import (
        _CONTEXT_REF_RE,
        _OBSERVABILITY_CAPTURES,
        SKILL_TOOLS,
        DataFlowWarning,
        _context_refs_in_value,
        _detect_stale_captured_paths,
        _is_observability_capture,
    )  # noqa: F401
    from ._analysis_graph import RECIPE_TERMINAL_TARGETS  # noqa: F401


def __getattr__(name: str):
    """Lazily resolve symbols and submodules to avoid eager subpackage loads."""
    if name in _LAZY_MODULES:
        from importlib import import_module

        full = f"{__name__}.{name}"
        mod = import_module(full)
        globals()[name] = mod
        return mod
    mod_name = _LAZY_SYMBOL_TO_MODULE.get(name)
    if mod_name is not None:
        from importlib import import_module

        mod = import_module(f"{__name__}.{mod_name}")
        value = getattr(mod, name)
        globals()[name] = value
        return value
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


_LAZY_MODULES: frozenset[str] = frozenset(
    {
        "_analysis",
        "_analysis_bfs",
        "_analysis_blocks",
        "_analysis_detectors",
        "_analysis_graph",
    }
)

_LAZY_SYMBOL_TO_MODULE: dict[str, str] = {
    "build_recipe_graph": "_analysis_graph",
    "RouteEdge": "_analysis_graph",
    "_extract_routing_edges": "_analysis_graph",
    "_build_step_graph": "_analysis_graph",
    "_build_raw_step_edges": "_analysis_graph",
    "_is_infrastructure_step": "_analysis_graph",
    "bfs_reachable": "_analysis_bfs",
    "_bfs_with_facts": "_analysis_bfs",
    "extract_blocks": "_analysis_blocks",
    "_detect_dead_outputs": "_analysis_detectors",
    "_detect_ref_invalidations": "_analysis_detectors",
    "_detect_implicit_handoffs": "_analysis_detectors",
    "ValidationContext": "_analysis",
    "analyze_dataflow": "_analysis",
    "make_validation_context": "_analysis",
    "iter_steps_with_context": "_analysis",
    "bfs_reachable_without_barrier": "_analysis_bfs",
    "bfs_reachable_without_barrier_in_graph": "_analysis_bfs",
    "all_paths_cross": "_analysis_bfs",
    "_build_success_step_graph": "_analysis_bfs",
    "_bfs_capped": "_analysis_bfs",
    "_build_capture_origin_map": "_analysis_bfs",
    "_INVALIDATING_TOOLS": "_analysis_bfs",
    "Recipe": "_analysis_graph",
    "RecipeBlock": "_analysis_blocks",
    "RecipeStep": "_analysis_graph",
    "_count_by_tool": "_analysis_blocks",
    "_count_gh_api": "_analysis_blocks",
    "get_logger": "_analysis_graph",
    "logger": "_analysis_graph",
    "DataFlowWarning": "_analysis_detectors",
    "SKILL_TOOLS": "_analysis_detectors",
    "_CONTEXT_REF_RE": "_analysis_detectors",
    "_OBSERVABILITY_CAPTURES": "_analysis_detectors",
    "_context_refs_in_value": "_analysis_detectors",
    "_detect_stale_captured_paths": "_analysis_detectors",
    "_is_observability_capture": "_analysis_detectors",
    "RECIPE_TERMINAL_TARGETS": "_analysis_graph",
}

__all__ = [
    "DataFlowWarning",
    "RECIPE_TERMINAL_TARGETS",
    "Recipe",
    "RecipeBlock",
    "RecipeStep",
    "RouteEdge",
    "SKILL_TOOLS",
    "ValidationContext",
    "_CONTEXT_REF_RE",
    "_INVALIDATING_TOOLS",
    "_OBSERVABILITY_CAPTURES",
    "_bfs_capped",
    "_bfs_with_facts",
    "_build_capture_origin_map",
    "_build_raw_step_edges",
    "_build_step_graph",
    "_build_success_step_graph",
    "_context_refs_in_value",
    "_count_by_tool",
    "_count_gh_api",
    "_detect_dead_outputs",
    "_detect_implicit_handoffs",
    "_detect_ref_invalidations",
    "_detect_stale_captured_paths",
    "_extract_routing_edges",
    "_is_infrastructure_step",
    "_is_observability_capture",
    "all_paths_cross",
    "analyze_dataflow",
    "bfs_reachable",
    "bfs_reachable_without_barrier",
    "bfs_reachable_without_barrier_in_graph",
    "build_recipe_graph",
    "extract_blocks",
    "get_logger",
    "iter_steps_with_context",
    "logger",
    "make_validation_context",
]
