"""Curated Phase A pipeline dependency derivation from recipe routing graphs.

Underscore-prefixed helper module (not ``tools_*.py``) — exempt from the
``tools_*.py`` import-namespace restriction (REQ-ARCH-003 / REQ-IMP-003),
which does not permit ``autoskillit.recipe`` imports. Same exemption class
as ``_auto_overrides.py`` / ``_serve_helpers.py``. See ``tools/AGENTS.md``.

Routing-edge extraction is inlined from public ``RecipeStep`` fields rather
than imported from ``recipe._analysis_graph`` — REQ-IMP-007 forbids
cross-package submodule imports; only the ``autoskillit.recipe`` package
gateway may be imported here, and the routing-edge helpers are private.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from autoskillit.recipe import RecipeStep

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


def _successor_targets(step: RecipeStep) -> set[str]:
    """Return the step names *step* routes to on success/failure/etc.

    Covers every routing field on ``RecipeStep``: ``on_success``,
    ``on_failure``, ``on_context_limit``, ``on_rate_limit``, ``on_exhausted``,
    and ``on_result`` (both ``conditions[].route`` and ``routes`` dict form).
    """
    targets: set[str] = set()
    if step.on_success:
        targets.add(step.on_success)
    if step.on_failure:
        targets.add(step.on_failure)
    if step.on_context_limit:
        targets.add(step.on_context_limit)
    if step.on_rate_limit:
        targets.add(step.on_rate_limit)
    if step.on_exhausted and step.action is None:
        targets.add(step.on_exhausted)
    if step.on_result:
        if step.on_result.routes:
            targets.update(step.on_result.routes.values())
        for condition in step.on_result.conditions:
            targets.add(condition.route)
    return targets


def _build_local_step_graph(active_steps: dict[str, RecipeStep]) -> dict[str, set[str]]:
    """Build a routing adjacency list scoped to *active_steps* only.

    Operates on a plain ``dict[str, RecipeStep]`` (the shape available at
    ``open_kitchen`` time via ``ctx.active_recipe_steps``). Edge targets
    outside *active_steps* are dropped.
    """
    step_names = set(active_steps)
    return {name: _successor_targets(step) & step_names for name, step in active_steps.items()}


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


def _derive_phase_a_deps(active_steps: dict[str, RecipeStep]) -> dict[str, list[str]]:
    """Derive curated, single-edge, loop-free step dependencies for Phase A.

    For each step name in ``_CURATED_TARGETS`` present in *active_steps*,
    find curated candidate predecessors that are direct predecessors of it
    in the routing graph built from *active_steps*. Steps involved in a
    cycle (e.g. the ``test -> check_test_fix_loop -> assess -> test`` or
    ``next_or_done -> dry_walkthrough`` loops) are skipped entirely, since
    Phase A only derives single-edge, loop-free prerequisites.
    """
    graph = _build_local_step_graph(active_steps)
    cycle_members = _find_cycle_members(graph)

    predecessors: dict[str, set[str]] = {name: set() for name in graph}
    for name, successors in graph.items():
        for succ in successors:
            predecessors[succ].add(name)

    deps: dict[str, list[str]] = {}
    for step_name, candidates in _CURATED_TARGETS.items():
        if step_name not in active_steps or step_name in cycle_members:
            continue
        matched = sorted(
            pred
            for pred in predecessors.get(step_name, set())
            if pred in candidates and pred not in cycle_members
        )
        if matched:
            deps[step_name] = matched
    return deps
