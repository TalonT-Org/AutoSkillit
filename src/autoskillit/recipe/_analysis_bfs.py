"""BFS reachability and symbolic fact-propagation over recipe step graphs."""

from __future__ import annotations

import re
from collections import deque
from typing import TYPE_CHECKING

from autoskillit.recipe.contracts import RESULT_CAPTURE_RE
from autoskillit.recipe.schema import Recipe

if TYPE_CHECKING:
    pass


# ---------------------------------------------------------------------------
# BFS helpers
# ---------------------------------------------------------------------------


def bfs_reachable(graph: dict[str, set[str]], start: str) -> set[str]:
    """Return all step names reachable from ``start`` in the routing graph (excluding start)."""
    visited: set[str] = set()
    queue = list(graph.get(start, set()))
    while queue:
        node = queue.pop()
        if node in visited:
            continue
        visited.add(node)
        queue.extend(graph.get(node, set()))
    return visited


def _build_capture_origin_map(recipe: Recipe) -> dict[str, str]:
    """Return {context_var_name: result_key} for all capture declarations.

    Only captures with a single extractable result.KEY reference are included.
    Used to determine provenance of context variables — specifically whether
    a variable was sourced from an ephemeral resource key like worktree_path
    or branch_name.
    """
    origin: dict[str, str] = {}
    for step in recipe.steps.values():
        for cap_var, cap_expr in (step.capture or {}).items():
            if isinstance(cap_expr, str):
                keys = RESULT_CAPTURE_RE.findall(cap_expr)
                if len(keys) == 1:
                    origin[cap_var] = keys[0]
    return origin


# Keys that become invalid after the given tool runs
_INVALIDATING_TOOLS: dict[str, frozenset[str]] = {
    "merge_worktree": frozenset({"worktree_path", "branch_name"}),
    "remove_clone": frozenset({"clone_path"}),
}


def _bfs_capped(
    graph: dict[str, set[str]],
    start_nodes: set[str],
    barrier_nodes: set[str],
) -> set[str]:
    """BFS from start_nodes, visiting but not expanding barrier_nodes.

    Used to find steps reachable from a starting set where certain steps
    (re-capture barriers) reset a variable's provenance — steps reachable
    only through a barrier are excluded from the result's expansion.

    Returns all visited nodes (including barrier_nodes that were reached).
    """
    visited: set[str] = set()
    queue = list(start_nodes)
    while queue:
        node = queue.pop()
        if node in visited:
            continue
        visited.add(node)
        if node in barrier_nodes:
            continue  # Reached but do not expand — variable is refreshed here
        queue.extend(graph.get(node, set()))
    return visited


# ---------------------------------------------------------------------------
# Symbolic reachability: BFS with fact propagation
# ---------------------------------------------------------------------------

# A FactSet is a frozen set of (variable, value) pairs established by
# conditional on_result.when edges.  Each frozenset represents the facts
# that are known-to-be-true on one particular path through the routing graph.
_FactSet = frozenset[tuple[str, str]]

# Matches simple equality conditions of the form:
#   context.X == 'v'    or    ${{ context.X }} == "v"
# Capture groups: 1 = variable name, 2 = value.
# Non-equality expressions (inequalities, conjunctions) produce no match —
# conservative assumption (no fact is established).
_SIMPLE_WHEN_RE = re.compile(
    r"(?:\$\{\{\s*)?context\.(\w+)(?:\s*\}\})?\s*==\s*"
    r"(?:\"(\w+)\"|'(\w+)'|(\w+))"
)


def _parse_when_expr(expr: str) -> tuple[str, str] | None:
    """Parse a simple equality when-expression into a (variable, value) fact.

    Returns ``None`` for conjunctions, inequalities, or non-context refs.
    Conservative: only establishes facts for provably-simple equality conditions.
    """
    m = _SIMPLE_WHEN_RE.fullmatch(expr.strip())
    if not m:
        return None
    # Groups 2/3/4 are mutually exclusive (double-quoted, single-quoted, unquoted)
    value = m.group(2) or m.group(3) or m.group(4)
    return (m.group(1), value)


def _edge_fact(recipe: Recipe, source: str, target: str) -> tuple[str, str] | None:
    """Return the (variable, value) fact established by the edge source→target, or None.

    Only ``on_result.conditions`` edges with a parseable simple equality ``when``
    expression contribute a fact.  ``on_success``/``on_failure`` edges contribute no fact.
    """
    step = recipe.steps.get(source)
    if step is None or step.on_result is None:
        return None
    for cond in step.on_result.conditions:
        if cond.route == target and cond.when is not None:
            return _parse_when_expr(cond.when)
    return None


def _intersect_facts(fs: set[_FactSet]) -> _FactSet:
    """Intersect all fact sets — only facts that hold on every incoming path survive."""
    if not fs:
        return frozenset()
    return frozenset.intersection(*fs)


def _bfs_with_facts(
    graph: dict[str, set[str]],
    recipe: Recipe,
    start: str,
) -> dict[str, set[_FactSet]]:
    """BFS from *start* propagating conditional edge facts.

    Each ``on_result`` edge whose ``when`` expression parses as
    ``'context.X == "v"'`` extends the current fact set with ``(X, v)`` on
    the target; other edges carry facts unchanged.  At join points, the
    returned fact set is the intersection of all incoming fact sets — a fact
    is only "known" at a node if it holds on every path reaching that node.

    Returns ``{step_name: {intersected_fact_set}}``.  Each value is a
    single-element set containing one :class:`frozenset` of ``(var, val)``
    pairs.
    """
    # facts maps step_name → set of fact-sets that have been discovered for it.
    # visited tracks (node, fact_set) pairs to avoid reprocessing.
    facts: dict[str, set[_FactSet]] = {start: {frozenset()}}
    work: deque[str] = deque([start])
    visited: set[tuple[str, _FactSet]] = set()

    while work:
        node = work.popleft()
        for succ in graph.get(node, ()):
            edge_fact = _edge_fact(recipe, node, succ)
            for f in facts.get(node, {frozenset()}):
                new_f: _FactSet = f | {edge_fact} if edge_fact else f
                state = (succ, new_f)
                if state in visited:
                    continue
                visited.add(state)
                facts.setdefault(succ, set()).add(new_f)
                work.append(succ)

    return {n: {_intersect_facts(fs)} for n, fs in facts.items()}
