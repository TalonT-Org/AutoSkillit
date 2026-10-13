"""Produce tracker dependencies from recipe routes and caller requests.

Underscore-prefixed helper module (not ``tools_*.py``) — exempt from the
``tools_*.py`` import-namespace restriction (REQ-ARCH-003 / REQ-IMP-003),
which does not permit ``autoskillit.recipe`` imports. Same exemption class
as ``_auto_overrides.py`` / ``_serve_helpers.py``. See ``tools/AGENTS.md``.

The finalized projection is the single authority for executable routes.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from autoskillit.core import (
    FinalizedRecipeProjection,
    dependency_cycle_members,
    get_logger,
    is_tracker_enforced_step,
)

logger = get_logger(__name__)

# Curated Phase A dependency targets: step_name -> candidate predecessor step
# names that satisfy the dependency when they are a *direct* predecessor of
# step_name in the recipe's routing graph. This is curated (not full
# ancestor-reachability) — targeting specifically the steps where
# out-of-order execution has been observed or is structurally likely. Full
# ancestor-reachability with cycle exemption is Phase C (separate design
# pass requiring SCC detection and per-iteration status semantics).
_CURATED_TARGETS: dict[str, frozenset[str]] = {
    "review_approach": frozenset({"rectify", "make_plan"}),
    "dry_walkthrough": frozenset({"rectify", "make_plan"}),
}


def _curated_single_predecessors(
    projection: FinalizedRecipeProjection | None,
) -> dict[str, list[str]]:
    """Select curated, single-edge, loop-free predecessors from the recipe graph.

    Steps involved in a cycle are skipped. Multiple matching predecessors are
    branch alternatives that the all-of tracker cannot represent, so they are
    reported and deliberately left without a derived dependency.
    """
    if projection is None:
        return {}

    step_names = set(projection.ordered_step_names)
    graph: dict[str, set[str]] = {name: set() for name in projection.ordered_step_names}
    for edge in projection.ordered_flow_edges:
        if edge.target in step_names:
            graph[edge.source].add(edge.target)
    cycle_members = dependency_cycle_members(graph)

    predecessors: dict[str, set[str]] = {name: set() for name in graph}
    for name, successors in graph.items():
        for succ in successors:
            predecessors[succ].add(name)

    deps: dict[str, list[str]] = {}
    for step_name, candidates in _CURATED_TARGETS.items():
        if step_name not in graph or step_name in cycle_members:
            continue
        matched = sorted(
            pred
            for pred in predecessors.get(step_name, set())
            if pred in candidates and pred not in cycle_members
        )
        if len(matched) == 1:
            deps[step_name] = matched
        elif len(matched) > 1:
            logger.warning(
                "pipeline_dependency_ambiguous_predecessors",
                step_name=step_name,
                predecessors=matched,
            )
    return deps


def _derive_phase_a_deps(
    projection: FinalizedRecipeProjection | None,
) -> dict[str, list[str]]:
    """Derive curated Phase A dependencies that the tracker can enforce."""
    if projection is None:
        return {}

    steps = {step.name: step for step in projection.ordered_steps}
    dependencies: dict[str, list[str]] = {}
    for dependent, prerequisites in _curated_single_predecessors(projection).items():
        for prerequisite in prerequisites:
            if is_tracker_enforced_step(steps.get(dependent)) and is_tracker_enforced_step(
                steps.get(prerequisite)
            ):
                dependencies[dependent] = [prerequisite]
            else:
                logger.warning(
                    "pipeline_dependency_unenforceable_step",
                    dependent=dependent,
                    prerequisite=prerequisite,
                )
    return dependencies


def _tracker_dependencies(
    projection: FinalizedRecipeProjection | None,
    requested: Mapping[str, Sequence[str]] | None = None,
) -> dict[str, list[str]]:
    """Combine enforceable recipe dependencies with distinct caller requests."""
    dependencies = {
        dependent: list(prerequisites)
        for dependent, prerequisites in _derive_phase_a_deps(projection).items()
    }
    for dependent, prerequisites in (requested or {}).items():
        if not prerequisites:
            continue
        stored = dependencies.setdefault(dependent, [])
        for prerequisite in prerequisites:
            if prerequisite not in stored:
                stored.append(prerequisite)
    return dependencies
