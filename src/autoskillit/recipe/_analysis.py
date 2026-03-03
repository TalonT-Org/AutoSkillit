"""Data-flow analysis for recipe pipelines.

Extracted from validator.py to break the circular import between
validator.py and rules.py (which needed to defer-import analyze_dataflow
and _build_step_graph to avoid the cycle).

Import chain: _analysis.py → contracts.py, io.py, schema.py
Neither contracts.py nor io.py imports _analysis.py, so no cycle exists.
"""

from __future__ import annotations

from autoskillit.core import SKILL_TOOLS, get_logger
from autoskillit.recipe.contracts import _CONTEXT_REF_RE, _RESULT_CAPTURE_RE
from autoskillit.recipe.io import iter_steps_with_context  # noqa: F401 — re-exported for rules
from autoskillit.recipe.schema import DataFlowReport, DataFlowWarning, Recipe

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# Step routing graph
# ---------------------------------------------------------------------------


def _build_step_graph(recipe: Recipe) -> dict[str, set[str]]:
    """Build a routing adjacency list from all step routing fields.

    Each key is a step name, each value is the set of step names
    reachable in one hop (successors). Terminal targets like "done"
    are excluded since they are not real steps.
    """
    step_names = set(recipe.steps.keys())
    graph: dict[str, set[str]] = {name: set() for name in step_names}

    for name, step in recipe.steps.items():
        for target in (step.on_success, step.on_failure, step.on_retry):
            if target and target in step_names:
                graph[name].add(target)
        if step.on_result:
            if step.on_result.routes:
                for target in step.on_result.routes.values():
                    if target in step_names:
                        graph[name].add(target)
            for condition in step.on_result.conditions:
                if condition.route in step_names:
                    graph[name].add(condition.route)
        if step.retry and step.retry.on_exhausted in step_names:
            graph[name].add(step.retry.on_exhausted)

    # Build predecessor map for bypass edge injection below.
    predecessors: dict[str, set[str]] = {name: set() for name in step_names}
    for name, successors in graph.items():
        for s in successors:
            predecessors[s].add(name)

    # For each step with skip_when_false, add bypass edges from all predecessors
    # directly to the step's routing targets (the steps to route to when skipped).
    # This makes optional-step bypass paths visible to graph-based rules.
    for name, step in recipe.steps.items():
        if not step.skip_when_false:
            continue
        # on_success bypass: predecessor → step.on_success
        if step.on_success and step.on_success in step_names:
            for pred in predecessors[name]:
                graph[pred].add(step.on_success)
        # on_result bypass: predecessor → each on_result condition route target
        if step.on_result:
            if step.on_result.routes:
                for target in step.on_result.routes.values():
                    if target in step_names:
                        for pred in predecessors[name]:
                            graph[pred].add(target)
            for condition in step.on_result.conditions:
                if condition.route in step_names:
                    for pred in predecessors[name]:
                        graph[pred].add(condition.route)

    return graph


# ---------------------------------------------------------------------------
# BFS helpers
# ---------------------------------------------------------------------------


def _bfs_reachable(graph: dict[str, set[str]], start: str) -> set[str]:
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
                keys = _RESULT_CAPTURE_RE.findall(cap_expr)
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
# Detection helpers
# ---------------------------------------------------------------------------


def _detect_ref_invalidations(recipe: Recipe, graph: dict[str, set[str]]) -> list[DataFlowWarning]:
    """Detect context variables consumed after the step that invalidated the
    underlying resource.

    The resource lifecycle contract:
    - ``merge_worktree`` on SUCCESS destroys the worktree directory and branch ref.
    - ``remove_clone`` on SUCCESS destroys the clone directory.

    Only steps reachable via ``on_success`` from the invalidating step are checked.
    Steps that re-capture the same variable are treated as barriers — they refresh
    the variable to a new resource, so their successors are excluded from
    the stale-ref check.
    """
    origin = _build_capture_origin_map(recipe)

    # Map: result_key → set of context variable names sourced from that key
    key_to_vars: dict[str, set[str]] = {}
    for var, result_key in origin.items():
        key_to_vars.setdefault(result_key, set()).add(var)

    # Map: var_name → set of step names that re-capture (refresh) it
    var_recapture_steps: dict[str, set[str]] = {}
    for step_name, step in recipe.steps.items():
        for cap_var in step.capture or {}:
            var_recapture_steps.setdefault(cap_var, set()).add(step_name)

    warnings: list[DataFlowWarning] = []

    for step_name, step in recipe.steps.items():
        invalidated_result_keys = _INVALIDATING_TOOLS.get(step.tool or "", frozenset())
        if not invalidated_result_keys:
            continue

        # Variables whose underlying resource is destroyed when this step SUCCEEDS
        invalidated_vars: set[str] = set()
        for result_key in invalidated_result_keys:
            invalidated_vars.update(key_to_vars.get(result_key, set()))

        if not invalidated_vars:
            continue

        # Only check steps reachable via on_success (failure path = resource not destroyed)
        on_success_target = step.on_success
        if not on_success_target or on_success_target not in recipe.steps:
            continue

        for var in invalidated_vars:
            # Steps that re-capture this var are barriers: they refresh the variable
            # to a new resource, so their successors are NOT stale consumers.
            barrier = var_recapture_steps.get(var, set())
            stale_reachable = _bfs_capped(graph, {on_success_target}, barrier)
            # A loop may route the invalidating step back into the reachable set
            stale_reachable.discard(step_name)

            for downstream_name in stale_reachable:
                downstream = recipe.steps.get(downstream_name)
                if downstream is None:
                    continue

                for arg_val in (downstream.with_args or {}).values():
                    if not isinstance(arg_val, str):
                        continue
                    for ref_var in _CONTEXT_REF_RE.findall(arg_val):
                        if ref_var == var:
                            warnings.append(
                                DataFlowWarning(
                                    code="REF_INVALIDATED",
                                    step_name=downstream_name,
                                    field=var,
                                    message=(
                                        f"Step '{downstream_name}' references "
                                        f"context.{var} after step '{step_name}' "
                                        f"({step.tool}) has invalidated the underlying "
                                        f"resource. Replace with a stable alternative "
                                        f"(e.g., a commit SHA captured before any merge "
                                        f"begins)."
                                    ),
                                )
                            )

    return warnings


def _detect_dead_outputs(recipe: Recipe, graph: dict[str, set[str]]) -> list[DataFlowWarning]:
    """Detect captured variables that are never consumed downstream."""
    warnings: list[DataFlowWarning] = []

    for step_name, step in recipe.steps.items():
        if not step.capture:
            continue

        # BFS: collect all steps reachable from this step
        reachable = _bfs_reachable(graph, step_name)

        # Collect all context.X references in reachable steps' with_args
        consumed: set[str] = set()
        for reachable_name in reachable:
            reachable_step = recipe.steps[reachable_name]
            for arg_val in reachable_step.with_args.values():
                if not isinstance(arg_val, str):
                    continue
                consumed.update(_CONTEXT_REF_RE.findall(arg_val))

        # on_result routing on a captured key is structural self-consumption
        if step.on_result and step.on_result.field in step.capture:
            consumed.add(step.on_result.field)

        # Flag captured vars not consumed on any path
        for cap_key in step.capture:
            if cap_key not in consumed:
                # Exempt merge_worktree diagnostic captures: cleanup_succeeded is captured
                # for observability (to surface orphaned worktrees), not for data-passing.
                # The merge-cleanup-uncaptured rule requires this capture; exempting it
                # from dead-output prevents the two rules from conflicting.
                cap_val = step.capture.get(cap_key, "")
                if step.tool == "merge_worktree" and "result.cleanup_succeeded" in str(cap_val):
                    continue
                warnings.append(
                    DataFlowWarning(
                        code="DEAD_OUTPUT",
                        step_name=step_name,
                        field=cap_key,
                        message=(
                            f"Step '{step_name}' captures '{cap_key}' but no "
                            f"reachable downstream step references "
                            f"${{{{ context.{cap_key} }}}}."
                        ),
                    )
                )

    return warnings


def _detect_implicit_handoffs(recipe: Recipe) -> list[DataFlowWarning]:
    """Detect skill-invoking steps with no capture block."""
    warnings: list[DataFlowWarning] = []

    for step_name, step in recipe.steps.items():
        if step.tool in SKILL_TOOLS and not step.capture:
            warnings.append(
                DataFlowWarning(
                    code="IMPLICIT_HANDOFF",
                    step_name=step_name,
                    field=step.tool,
                    message=(
                        f"Step '{step_name}' calls '{step.tool}' but has no "
                        f"capture: block. Data flows to subsequent steps "
                        f"implicitly through agent context rather than "
                        f"explicit ${{{{ context.X }}}} wiring."
                    ),
                )
            )

    return warnings


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def analyze_dataflow(recipe: Recipe) -> DataFlowReport:
    """Analyze pipeline data flow quality (non-blocking warnings)."""
    graph = _build_step_graph(recipe)

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
