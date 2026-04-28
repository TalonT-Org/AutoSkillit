"""Data-flow analysis for recipe pipelines.

Extracted from validator.py to break the circular import between
validator.py and rules.py (which needed to defer-import analyze_dataflow
and _build_step_graph to avoid the cycle).

Import chain: _analysis.py → contracts.py, io.py, schema.py
Neither contracts.py nor io.py imports _analysis.py, so no cycle exists.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from autoskillit.recipe._analysis_bfs import _bfs_with_facts, bfs_reachable
from autoskillit.recipe._analysis_blocks import extract_blocks
from autoskillit.recipe._analysis_detectors import (
    _detect_dead_outputs,
    _detect_implicit_handoffs,
    _detect_ref_invalidations,
)
from autoskillit.recipe._analysis_graph import (
    RouteEdge,
    _build_step_graph,
    _extract_routing_edges,
    _is_infrastructure_step,
    build_recipe_graph,
)
from autoskillit.recipe.io import iter_steps_with_context  # noqa: F401 — re-exported for rules
from autoskillit.recipe.schema import (
    DataFlowReport,
    DataFlowWarning,
    Recipe,
    RecipeBlock,
)

# Re-export all symbols that external code currently imports from this module.
__all__ = [
    "build_recipe_graph",
    "RouteEdge",
    "_extract_routing_edges",
    "_build_step_graph",
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
    available_recipes: frozenset[str] = field(default_factory=frozenset)
    available_skills: frozenset[str] = field(default_factory=frozenset)
    available_sub_recipes: frozenset[str] = field(default_factory=frozenset)
    project_dir: Path | None = None
    disabled_subsets: frozenset[str] = field(default_factory=frozenset)
    disabled_features: frozenset[str] = field(default_factory=frozenset)
    skill_category_map: dict[str, frozenset[str]] | None = None
    overridden_skills: frozenset[str] | None = None
    blocks: tuple[RecipeBlock, ...] = field(default_factory=tuple)
    predecessors: dict[str, set[str]] = field(default_factory=dict)


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
) -> ValidationContext:
    """Build a ``ValidationContext`` from a recipe.

    Constructs the step graph and data-flow report once so that semantic
    rules can share the pre-built objects without redundant computation.
    """
    step_graph = _build_step_graph(recipe)
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
        blocks=extract_blocks(recipe, step_graph, predecessors=predecessors),
        predecessors=predecessors,
    )
