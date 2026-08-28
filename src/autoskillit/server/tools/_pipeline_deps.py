"""Curated Phase A dependencies from the finalized recipe routing graph.

Underscore-prefixed helper module (not ``tools_*.py``) — exempt from the
``tools_*.py`` import-namespace restriction (REQ-ARCH-003 / REQ-IMP-003),
which does not permit ``autoskillit.recipe`` imports. Same exemption class
as ``_auto_overrides.py`` / ``_serve_helpers.py``. See ``tools/AGENTS.md``.

The finalized projection is the single authority for executable routes.
"""

from __future__ import annotations

from autoskillit.core import FinalizedRecipeProjection, get_logger

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


def _find_cycle_members(graph: dict[str, set[str]]) -> set[str]:
    """Return step names that participate in a cycle, via DFS back-edge detection."""
    WHITE, GRAY, BLACK = 0, 1, 2
    color: dict[str, int] = dict.fromkeys(graph, WHITE)
    cycle_members: set[str] = set()

    def visit(node: str, stack: list[str]) -> None:
        color[node] = GRAY
        stack.append(node)
        for neighbor in graph.get(node, ()):
            if color.get(neighbor) == GRAY:
                idx = stack.index(neighbor)
                cycle_members.update(stack[idx:])
            elif color.get(neighbor) == WHITE:
                visit(neighbor, stack)
        stack.pop()
        color[node] = BLACK

    for node in graph:
        if color[node] == WHITE:
            visit(node, [])
    return cycle_members


def _derive_phase_a_deps(
    projection: FinalizedRecipeProjection | None,
) -> dict[str, list[str]]:
    """Derive curated, single-edge, loop-free step dependencies for Phase A.

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
    cycle_members = _find_cycle_members(graph)

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
