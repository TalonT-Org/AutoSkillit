"""BFS reachability and symbolic fact-propagation over recipe step graphs."""

from __future__ import annotations

from collections import deque

import regex as re

from autoskillit.recipe._analysis_graph import _build_step_graph
from autoskillit.recipe.contracts import RESULT_CAPTURE_RE
from autoskillit.recipe.schema import Recipe

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
            keys = RESULT_CAPTURE_RE.findall(cap_expr.from_)
            if len(keys) == 1:
                origin[cap_var] = keys[0]
        for cap_var, cap_expr in (step.capture_list or {}).items():
            keys = RESULT_CAPTURE_RE.findall(cap_expr.from_)
            if len(keys) == 1:
                origin[cap_var] = keys[0]
    return origin


# Keys that become invalid after the given tool runs
_INVALIDATING_TOOLS: dict[str, frozenset[str]] = {
    "merge_worktree": frozenset({"worktree_path", "branch_name"}),
    "remove_clone": frozenset({"clone_path"}),
}


def _build_success_step_graph(recipe: Recipe) -> dict[str, set[str]]:
    """Build an adjacency dict from recipe step routing edges, success paths only.

    Includes on_result condition routes and on_success edges.
    Excludes on_failure and on_context_limit — those are error-recovery paths,
    not verdict-driven routing, and must not be checked by waypoint invariants.
    """
    graph: dict[str, set[str]] = {name: set() for name in recipe.steps}
    for step_name, step in recipe.steps.items():
        if step.on_result and step.on_result.conditions:
            for cond in step.on_result.conditions:
                if cond.route:
                    graph[step_name].add(cond.route)
        if step.on_success:
            graph[step_name].add(step.on_success)
    return graph


def bfs_reachable_without_barrier(
    recipe: Recipe,
    start: str,
    barrier: str | frozenset[str],
    *,
    ignored_edges: frozenset[tuple[str, str]] = frozenset(),
) -> set[str]:
    """BFS from ``start`` through success-path routing edges, stopping at ``barrier``.

    Returns all step names reachable from ``start`` without crossing ``barrier``.
    The barrier step itself is included in the returned set — it is visited but
    not expanded beyond.

    ``barrier`` may be a single step name (str) or a frozenset of step names.
    When a frozenset is provided, any of the named steps acts as a barrier.

    ``ignored_edges`` removes explicitly exempted success-path edges before
    traversal. Callers must prove an edge is exempt rather than broadly treating
    its target as a barrier, which would hide other paths to the same target.

    Only follows on_result conditions and on_success edges — error paths
    (on_failure, on_context_limit) are excluded because they are not
    verdict-driven routing and must not be checked by waypoint invariants.

    This is the canonical implementation of the BFS-barrier pattern previously
    duplicated inline in ``push-before-audit`` and ``merge-base-unpublished``.
    """
    barriers: set[str] = {barrier} if isinstance(barrier, str) else set(barrier)
    graph = _build_success_step_graph(recipe)
    for source, target in ignored_edges:
        graph.get(source, set()).discard(target)
    return _bfs_capped(graph, {start}, barriers)


def bfs_reachable_without_barrier_in_graph(
    graph: dict[str, set[str]],
    start: str,
    barrier: frozenset[str],
) -> set[str]:
    """BFS over a caller-supplied adjacency, visiting but not expanding barriers.

    Unlike bfs_reachable_without_barrier, the caller chooses the edge model —
    pass ctx.step_graph to traverse failure/context-limit/rate-limit edges too.
    """
    return _bfs_capped(graph, {start}, set(barrier))


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


def all_paths_cross(
    graph: dict[str, set[str]],
    start: str,
    candidate: str,
    target: str,
) -> bool:
    """Return True iff every path from ``start`` to ``target`` in ``graph`` crosses ``candidate``.

    Implements the contradiction pattern: if ``target`` is unreachable from
    ``start`` when ``candidate`` is treated as a barrier (visited but not
    expanded), then every path from ``start`` to ``target`` must cross
    ``candidate``. This promotes the inline pattern previously duplicated in
    ``capture-inversion-detection`` and ``clone-terminal-requires-registration``
    into a named, tested, reusable helper.

    The caller controls which edge types are included in ``graph`` — typically
    the same adjacency used for candidate selection (e.g. ``ctx.step_graph``
    for all-edges reachability, or a narrower success-only graph). Mixing the
    graphs is unsupported: dominance claims are only valid on the graph that
    was searched.

    ``ctx.step_graph`` specifically (built by ``_build_step_graph``) includes
    bypass edges injected for ``skip_when_false`` steps and ``sub_recipe``
    placeholder steps, in addition to the six routing-field edge types
    (``on_result``, ``on_success``, ``on_failure``, ``on_context_limit``,
    ``on_rate_limit``, ``on_exhausted``). These bypass edges can create
    shortcuts around a candidate dominator node — callers should be aware
    of this when interpreting a ``True``/``False`` result against
    ``ctx.step_graph``.

    Returns False if ``target`` is not reachable from ``start`` in the
    unmodified graph. This guards against vacuous-true dominance results from
    unreachable targets — the standard "not-in-reachable-set implies dominates"
    idiom is unsound without this precondition.

    Trivially returns True when ``candidate == target`` (a node dominates
    itself). Callers that derive candidate lists from a predecessor set that
    includes the target itself must filter the target out before invoking,
    or accept the vacuous True.
    """
    if start not in graph and start not in (candidate, target):
        # start is not a node in the graph — unreachable trivially
        return False
    full_reachable = bfs_reachable(graph, start)
    if target not in full_reachable and target != start:
        return False
    if candidate == target:
        return True
    reachable_without = _bfs_capped(graph, {start}, {candidate})
    return target not in reachable_without


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
