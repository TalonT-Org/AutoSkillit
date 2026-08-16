"""Data-flow analysis for recipe pipelines.

Extracted from validator.py to break the circular import between
validator.py and rules.py (which needed to defer-import analyze_dataflow
and _build_step_graph to avoid the cycle).

Import chain: _analysis.py → contracts.py, io.py, schema.py
Neither contracts.py nor io.py imports _analysis.py, so no cycle exists.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from autoskillit.core import BoundScalar, RecipeBindingProjection

if TYPE_CHECKING:
    from autoskillit.core import BackendCapabilities, SkillResolver

from autoskillit.recipe._analysis_bfs import _bfs_with_facts, bfs_reachable
from autoskillit.recipe._analysis_blocks import extract_blocks
from autoskillit.recipe._analysis_detectors import (
    _detect_dead_outputs,
    _detect_implicit_handoffs,
    _detect_ref_invalidations,
    _detect_stale_captured_paths,
)
from autoskillit.recipe._analysis_graph import (
    RouteEdge,
    _build_raw_step_edges,
    _build_step_graph,
    _extract_routing_edges,
    _is_infrastructure_step,
    build_recipe_graph,
)
from autoskillit.recipe._binding import bind_recipe
from autoskillit.recipe.io import iter_steps_with_context  # noqa: F401 — re-exported for rules
from autoskillit.recipe.schema import (
    DataFlowReport,
    DataFlowWarning,
    Recipe,
    RecipeBlock,
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


@dataclass
class ValidationContext:
    """Shared computation for a single validation pass.

    Built once per ``run_semantic_rules`` invocation so that rules consuming
    the step graph or dataflow report do not repeat those expensive builds.
    """

    recipe: Recipe
    step_graph: dict[str, set[str]]
    dataflow: DataFlowReport
    binding_projection: RecipeBindingProjection
    available_recipes: frozenset[str] = field(default_factory=frozenset)
    available_skills: frozenset[str] = field(default_factory=frozenset)
    available_sub_recipes: frozenset[str] = field(default_factory=frozenset)
    project_dir: Path | None = None
    disabled_subsets: frozenset[str] = field(default_factory=frozenset)
    disabled_features: frozenset[str] = field(default_factory=frozenset)
    provider_profiles: frozenset[str] = field(default_factory=frozenset)
    skill_category_map: dict[str, frozenset[str]] | None = None
    overridden_skills: frozenset[str] | None = None
    backend_name: str | None = None
    skill_resolver: SkillResolver | None = None
    effective_backend_map: dict[str, str] | None = None
    backend_capabilities_map: dict[str, BackendCapabilities] | None = None
    backend_origin_map: dict[str, str] | None = None
    blocks: tuple[RecipeBlock, ...] = field(default_factory=tuple)
    predecessors: dict[str, set[str]] = field(default_factory=dict)
    must_defined_context: dict[str, frozenset[str]] = field(default_factory=dict)
    predecessor_edges: dict[str, tuple[tuple[str, RouteEdge], ...]] = field(default_factory=dict)


def _must_definition_facts(
    recipe: Recipe,
    raw_edges: dict[str, tuple[RouteEdge, ...]],
) -> tuple[
    dict[str, frozenset[str]],
    dict[str, tuple[tuple[str, RouteEdge], ...]],
]:
    """Compute context names guaranteed to be defined on every path to each step."""
    if not recipe.steps:
        return {}, {}
    predecessor_edges: dict[str, list[tuple[str, RouteEdge]]] = {name: [] for name in recipe.steps}
    for source, edges in raw_edges.items():
        for edge in edges:
            predecessor_edges[edge.target].append((source, edge))

    entry = next(iter(recipe.steps))
    reachable = {entry}
    reachability_queue: deque[str] = deque([entry])
    while reachability_queue:
        source = reachability_queue.popleft()
        for edge in raw_edges[source]:
            if edge.target not in reachable:
                reachable.add(edge.target)
                reachability_queue.append(edge.target)

    capture_domain = frozenset(
        capture
        for name in reachable
        for capture in (*recipe.steps[name].capture, *recipe.steps[name].capture_list)
    )
    facts = {name: capture_domain for name in reachable}
    facts[entry] = frozenset()
    queue: deque[str] = deque(name for name in recipe.steps if name in reachable)
    queued = set(queue)
    while queue:
        source = queue.popleft()
        queued.remove(source)
        for edge in raw_edges[source]:
            target = edge.target
            if target == entry or target not in reachable:
                continue
            incoming = [
                (
                    facts[predecessor]
                    | frozenset(recipe.steps[predecessor].capture)
                    | frozenset(recipe.steps[predecessor].capture_list)
                    if predecessor_edge.capture_available
                    else facts[predecessor]
                )
                for predecessor, predecessor_edge in predecessor_edges[target]
                if predecessor in reachable
            ]
            updated = frozenset.intersection(*incoming) if incoming else frozenset()
            if facts[target] != updated:
                facts[target] = updated
                if target not in queued:
                    queue.append(target)
                    queued.add(target)

    return (
        facts,
        {
            name: tuple(
                sorted(
                    (edge for edge in edges if edge[0] in reachable),
                    key=lambda item: (item[0], item[1].edge_type),
                )
            )
            for name, edges in predecessor_edges.items()
            if name in reachable
        },
    )


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def analyze_dataflow(
    recipe: Recipe,
    *,
    step_graph: dict[str, set[str]] | None = None,
) -> DataFlowReport:
    """Analyze pipeline data flow quality (non-blocking warnings).

    Args:
        recipe: The recipe to analyze.
        step_graph: Optional pre-built routing graph. When provided, the
            expensive ``_build_step_graph`` call is skipped.
    """
    graph = step_graph if step_graph is not None else _build_step_graph(recipe)

    warnings: list[DataFlowWarning] = []
    warnings.extend(_detect_dead_outputs(recipe, graph))
    warnings.extend(_detect_implicit_handoffs(recipe))
    warnings.extend(_detect_ref_invalidations(recipe, graph))
    warnings.extend(_detect_stale_captured_paths(recipe, graph))

    if warnings:
        summary = f"{len(warnings)} data-flow warning{'s' if len(warnings) != 1 else ''} found."
    else:
        summary = (
            "No data-flow warnings. All captures are consumed"
            " and skill outputs are explicitly wired."
        )

    return DataFlowReport(warnings=warnings, summary=summary)


def make_validation_context(
    recipe: Recipe,
    *,
    available_recipes: frozenset[str] = frozenset(),
    available_skills: frozenset[str] = frozenset(),
    available_sub_recipes: frozenset[str] = frozenset(),
    project_dir: Path | None = None,
    disabled_subsets: frozenset[str] = frozenset(),
    disabled_features: frozenset[str] = frozenset(),
    provider_profiles: frozenset[str] = frozenset(),
    backend_name: str | None = None,
    skill_resolver: SkillResolver | None = None,
    effective_backend_map: dict[str, str] | None = None,
    backend_capabilities_map: dict[str, BackendCapabilities] | None = None,
    backend_origin_map: dict[str, str] | None = None,
    binding_projection: RecipeBindingProjection | None = None,
    binding_ingredient_values: dict[str, BoundScalar] | None = None,
) -> ValidationContext:
    """Build a ``ValidationContext`` from a recipe.

    Constructs the step graph and data-flow report once so that semantic
    rules can share the pre-built objects without redundant computation.
    """
    raw_edges = _build_raw_step_edges(recipe)
    step_graph = {source: {edge.target for edge in edges} for source, edges in raw_edges.items()}
    must_defined_context, predecessor_edges = _must_definition_facts(recipe, raw_edges)
    dataflow = analyze_dataflow(recipe, step_graph=step_graph)
    # Build predecessor map once; also passed to extract_blocks to avoid
    # recomputing the same inversion inside that function.
    predecessors: dict[str, set[str]] = {}
    for src, successors in step_graph.items():
        for dst in successors:
            predecessors.setdefault(dst, set()).add(src)
    return ValidationContext(
        recipe=recipe,
        step_graph=step_graph,
        dataflow=dataflow,
        available_recipes=available_recipes,
        available_skills=available_skills,
        available_sub_recipes=available_sub_recipes,
        project_dir=project_dir,
        disabled_subsets=disabled_subsets,
        disabled_features=disabled_features,
        provider_profiles=provider_profiles,
        backend_name=backend_name,
        skill_resolver=skill_resolver,
        effective_backend_map=effective_backend_map,
        backend_capabilities_map=backend_capabilities_map,
        backend_origin_map=backend_origin_map,
        blocks=extract_blocks(recipe, step_graph, predecessors=predecessors),
        predecessors=predecessors,
        must_defined_context=must_defined_context,
        predecessor_edges=predecessor_edges,
        binding_projection=(
            binding_projection
            if binding_projection is not None
            else bind_recipe(recipe, ingredient_values=binding_ingredient_values)
        ),
    )
