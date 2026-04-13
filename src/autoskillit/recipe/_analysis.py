"""Data-flow analysis for recipe pipelines.

Extracted from validator.py to break the circular import between
validator.py and rules.py (which needed to defer-import analyze_dataflow
and _build_step_graph to avoid the cycle).

Import chain: _analysis.py → contracts.py, io.py, schema.py
Neither contracts.py nor io.py imports _analysis.py, so no cycle exists.
"""

from __future__ import annotations

import re
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path

import igraph

from autoskillit.core import SKILL_TOOLS, get_logger
from autoskillit.recipe.contracts import _CONTEXT_REF_RE, _RESULT_CAPTURE_RE
from autoskillit.recipe.io import iter_steps_with_context  # noqa: F401 — re-exported for rules
from autoskillit.recipe.schema import (
    _TERMINAL_TARGETS,
    DataFlowReport,
    DataFlowWarning,
    Recipe,
    RecipeBlock,
    RecipeStep,
)

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# Infrastructure step classification (shared with diagrams rendering)
# ---------------------------------------------------------------------------


def _is_infrastructure_step(step: RecipeStep) -> bool:
    """Return True if *step* is a plumbing step that should be hidden from diagrams.

    Infrastructure steps are ``run_cmd`` steps whose sole purpose is capturing
    or setting a context value (git rev-parse, printf, echo one-liners).
    They add no user-visible behaviour to the pipeline flow.
    """
    if step.tool != "run_cmd":
        return False
    note_lower = (step.note or "").lower()
    cmd = ""
    if step.with_args and isinstance(step.with_args, dict):
        cmd = step.with_args.get("cmd", "") or ""
    return (
        "capture" in note_lower
        or "set" in note_lower
        or "printf" in cmd
        or "git rev-parse" in cmd
        or (cmd.strip().startswith("echo") and "\n" not in cmd)
    )


# ---------------------------------------------------------------------------
# igraph recipe graph builder
# ---------------------------------------------------------------------------


def build_recipe_graph(recipe: Recipe) -> igraph.Graph:
    """Build a directed igraph.Graph from a Recipe dataclass.

    Nodes represent recipe steps. Each vertex carries attributes matching the
    RecipeStep fields relevant to diagram rendering:
    - ``name``: step name (str)
    - ``tool``: tool identifier (str, empty string if None)
    - ``action``: action identifier (str, empty string if None)
    - ``note``: step note for semantic gate checks (str)
    - ``retries``: retry count (int)
    - ``skip_when_false``: optional condition (str, empty string if None)
    - ``is_infra``: whether the step is a hidden infrastructure step (bool)
    - ``is_terminal``: whether the step is a stop action (bool)
    - ``is_confirm``: whether the step is a confirm action (bool)

    Edges represent routing connections. Each edge carries:
    - ``edge_type``: one of ``"success"``, ``"failure"``, ``"context_limit"``,
      ``"result_condition"``, ``"exhausted"``
    - ``condition``: for ``on_result`` edges, the ``when`` expression; otherwise ``""``

    Args:
        recipe: The loaded Recipe dataclass.

    Returns:
        A directed ``igraph.Graph`` with vertex and edge attributes as described.
    """
    step_names = list(recipe.steps.keys())
    name_to_id: dict[str, int] = {name: i for i, name in enumerate(step_names)}

    g = igraph.Graph(n=len(step_names), directed=True)
    steps_list = list(recipe.steps.values())

    g.vs["name"] = step_names
    g.vs["tool"] = [s.tool or "" for s in steps_list]
    g.vs["action"] = [s.action or "" for s in steps_list]
    g.vs["note"] = [s.note or "" for s in steps_list]
    g.vs["retries"] = [s.retries for s in steps_list]
    g.vs["skip_when_false"] = [s.skip_when_false or "" for s in steps_list]
    g.vs["is_infra"] = [_is_infrastructure_step(s) for s in steps_list]
    g.vs["is_terminal"] = [s.action == "stop" for s in steps_list]
    g.vs["is_confirm"] = [s.action == "confirm" for s in steps_list]

    edges: list[tuple[int, int]] = []
    edge_types: list[str] = []
    edge_conditions: list[str] = []

    for name, step in recipe.steps.items():
        src = name_to_id[name]
        for edge in _extract_routing_edges(step):
            # Mirror _build_step_graph: skip on_exhausted edges for action steps
            # (stop/confirm/route steps have no retry semantics).
            if edge.edge_type == "exhausted" and step.action is not None:
                continue
            if edge.target in name_to_id:
                edges.append((src, name_to_id[edge.target]))
                edge_types.append(edge.edge_type)
                edge_conditions.append(edge.condition or "")
            elif edge.target in _TERMINAL_TARGETS:
                # Known sentinel — valid target, no graph edge needed.
                pass
            else:
                logger.warning(
                    "build_recipe_graph: step %r references unknown target %r — edge skipped",
                    name,
                    edge.target,
                )

    if edges:
        g.add_edges(edges, attributes={"edge_type": edge_types, "condition": edge_conditions})

    return g


# ---------------------------------------------------------------------------
# Routing edge extraction
# ---------------------------------------------------------------------------


@dataclass
class RouteEdge:
    """A single routing edge from a recipe step to a target step.

    Attributes:
        edge_type: One of ``"success"``, ``"failure"``, ``"context_limit"``,
            ``"result_condition"``, ``"exhausted"``.
        target: The target step name.
        condition: Populated for ``on_result`` conditions — the ``when`` expression.
    """

    edge_type: str
    target: str
    condition: str | None = None


def _extract_routing_edges(step: RecipeStep) -> list[RouteEdge]:
    """Return all routing edges declared on *step*.

    Covers every routing field on :class:`RecipeStep`:
    ``on_success``, ``on_failure``, ``on_context_limit``, ``on_exhausted``,
    ``on_result.conditions[].route``, and ``on_result.routes`` (dict form).

    None targets are skipped. The caller is responsible for filtering by
    known step names if graph-membership checks are needed.
    """
    edges: list[RouteEdge] = []

    if step.on_success:
        edges.append(RouteEdge(edge_type="success", target=step.on_success))
    if step.on_failure:
        edges.append(RouteEdge(edge_type="failure", target=step.on_failure))
    if step.on_context_limit:
        edges.append(RouteEdge(edge_type="context_limit", target=step.on_context_limit))
    if step.on_exhausted:
        edges.append(RouteEdge(edge_type="exhausted", target=step.on_exhausted))

    if step.on_result:
        sr = step.on_result
        if sr.conditions:
            for cond in sr.conditions:
                edges.append(
                    RouteEdge(
                        edge_type="result_condition",
                        target=cond.route,
                        condition=cond.when,
                    )
                )
        elif sr.routes:
            for key, target in sr.routes.items():
                edges.append(
                    RouteEdge(
                        edge_type="result_condition",
                        target=target,
                        condition=key,
                    )
                )

    return edges


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
    skill_category_map: dict[str, frozenset[str]] | None = None
    overridden_skills: frozenset[str] | None = None
    blocks: tuple[RecipeBlock, ...] = field(default_factory=tuple)


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
        for edge in _extract_routing_edges(step):
            # exhausted only applies to non-terminal steps
            if edge.edge_type == "exhausted" and step.action is not None:
                continue
            if edge.target in step_names:
                graph[name].add(edge.target)

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

    # For each sub_recipe placeholder step (gate-controlled), add a bypass edge
    # to the next step in YAML order. When the gate is false the step is dropped
    # at load time; without this edge the next step becomes unreachable in the
    # raw recipe graph, breaking reachability-based semantic rules.
    step_names_list = list(recipe.steps.keys())
    for i, (name, step) in enumerate(recipe.steps.items()):
        if step.sub_recipe is None or i + 1 >= len(step_names_list):
            continue
        next_step = step_names_list[i + 1]
        graph[name].add(next_step)
        for pred in predecessors[name]:
            graph[pred].add(next_step)

    return graph


# ---------------------------------------------------------------------------
# Block extraction helpers
# ---------------------------------------------------------------------------


def _count_by_tool(members: list[RecipeStep]) -> dict[str, int]:
    """Count tool occurrences across a list of steps."""
    counts: dict[str, int] = {}
    for step in members:
        key = step.tool or ""
        if key:
            counts[key] = counts.get(key, 0) + 1
    return counts


def _count_gh_api(step: RecipeStep) -> int:
    """Count occurrences of 'gh api' in a run_cmd step's cmd argument."""
    if step.tool != "run_cmd":
        return 0
    cmd = (step.with_args or {}).get("cmd", "") or ""
    return cmd.count("gh api")


def extract_blocks(recipe: Recipe, step_graph: dict[str, set[str]]) -> tuple[RecipeBlock, ...]:
    """Extract named block regions from a recipe's routing graph.

    Groups steps that share the same ``step.block`` value, then computes the
    entry step (no in-block predecessor) and exit step (no in-block successor)
    for each group.  The step_graph is the forward adjacency dict produced by
    ``_build_step_graph``; a local inverse is built here to find predecessors.

    Steps without a ``block`` annotation are ignored.  Recipes with no block
    annotations return an empty tuple.
    """
    # Build predecessor map by inverting the forward step_graph edges.
    predecessors: dict[str, set[str]] = {}
    for src, successors in step_graph.items():
        for dst in successors:
            predecessors.setdefault(dst, set()).add(src)

    by_name: dict[str, list[RecipeStep]] = {}
    for step in recipe.steps.values():
        if step.block is not None:
            by_name.setdefault(step.block, []).append(step)

    blocks: list[RecipeBlock] = []
    for name, members in by_name.items():
        member_names = {s.name for s in members}
        # Entry: no in-block predecessor (predecessor set ∩ member_names is empty)
        entry_candidates = [
            s for s in members if not (predecessors.get(s.name, set()) & member_names)
        ]
        # Exit: no in-block successor (successor set ∩ member_names is empty)
        exit_candidates = [
            s for s in members if not (step_graph.get(s.name, set()) & member_names)
        ]
        blocks.append(
            RecipeBlock(
                name=name,
                entry=entry_candidates[0].name if entry_candidates else members[0].name,
                exit=exit_candidates[0].name if exit_candidates else members[-1].name,
                members=tuple(members),
                tool_counts=_count_by_tool(members),
                gh_api_occurrences=sum(_count_gh_api(s) for s in members),
            )
        )
    return tuple(blocks)


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
_SIMPLE_WHEN_RE = re.compile(r"(?:\$\{\{\s*)?context\.(\w+)(?:\s*\}\})?\s*==\s*[\"']?(\w+)[\"']?")


def _parse_when_expr(expr: str) -> tuple[str, str] | None:
    """Parse a simple equality when-expression into a (variable, value) fact.

    Returns ``None`` for conjunctions, inequalities, or non-context refs.
    Conservative: only establishes facts for provably-simple equality conditions.
    """
    m = _SIMPLE_WHEN_RE.fullmatch(expr.strip())
    return (m.group(1), m.group(2)) if m else None


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

        # Collect all context.X references in reachable steps' with_args and
        # on_result condition when-expressions (route actions gate on context vars).
        consumed: set[str] = set()
        for reachable_name in reachable:
            reachable_step = recipe.steps[reachable_name]
            for arg_val in reachable_step.with_args.values():
                if not isinstance(arg_val, str):
                    continue
                consumed.update(_CONTEXT_REF_RE.findall(arg_val))
            if reachable_step.on_result and reachable_step.on_result.conditions:
                for cond in reachable_step.on_result.conditions:
                    if cond.when and isinstance(cond.when, str):
                        consumed.update(_CONTEXT_REF_RE.findall(cond.when))

        # on_result routing — both legacy field and predicate conditions count
        # as structural consumption of captured variables.
        if step.on_result:
            # Legacy field routing: field name matches a captured key
            if step.on_result.field in step.capture:
                consumed.add(step.on_result.field)
            # Predicate condition routing — conditions gate on step result;
            # treat all captured vars as structurally consumed.
            if step.on_result.conditions:
                consumed.update(step.capture.keys())

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
                # Exempt diagnose-ci diagnosis_path captures: in recipes without a resolve_ci
                # step (e.g. merge-prs), diagnosis_path is captured for observability
                # only — no downstream automated remediation consumes it.
                if cap_key == "diagnosis_path" and "diagnose-ci" in step.with_args.get(
                    "skill_command", ""
                ):
                    continue
                # Exempt pipeline-summary summary_path captures: pipeline-summary is a
                # terminal reporting skill that writes to disk for human use; summary_path
                # is captured for observability only — no downstream step consumes it.
                if cap_key == "summary_path" and "pipeline-summary" in step.with_args.get(
                    "skill_command", ""
                ):
                    continue
                # Exempt generate-report report_path captures in terminal re-validation steps:
                # when report_path is captured from generate-report but no downstream step
                # references it (e.g. re_generate_report in a post-review re-validation loop
                # that routes directly to test then push), the capture satisfies the
                # implicit-handoff contract for observability — not downstream threading.
                if cap_key == "report_path" and "generate-report" in step.with_args.get(
                    "skill_command", ""
                ):
                    continue
                # Exempt note-driven lens iteration captures from prepare-research-pr and
                # prepare-pr: selected_lenses and lens_context_paths are consumed by
                # run_experiment_lenses / run_arch_lenses via the step's note field.
                # The note instructs the orchestrator to iterate over selected_lenses
                # values and match them with lens_context_paths paths.
                # Static dataflow analysis cannot detect note-driven consumption.
                if cap_key in (
                    "selected_lenses",
                    "lens_context_paths",
                ) and any(
                    s in step.with_args.get("skill_command", "")
                    for s in ("prepare-research-pr", "prepare-pr")
                ):
                    continue
                # Exempt compose-pr pr_url observability capture: pr_url is the terminal
                # output of the PR creation flow, captured for pipeline reporting and
                # post-tool hooks (e.g. token_summary_appender). No downstream recipe
                # step consumes it — consumption happens outside the recipe pipeline.
                if cap_key == "pr_url" and "compose-pr" in step.with_args.get("skill_command", ""):
                    continue
                # Exempt bundle-local-report html_path captures: html_path is captured for
                # observability and future groupH local-mode export (route_archive_or_export
                # step). No current downstream recipe step consumes it — static analysis
                # cannot yet verify the future consumption point.
                if cap_key == "html_path" and "bundle-local-report" in step.with_args.get(
                    "skill_command", ""
                ):
                    continue
                # Exempt export_local_bundle local_bundle_path captures: local_bundle_path
                # is captured for orchestrator observability. The route_archive_or_export
                # local-mode path ends at research_complete (a stop action), so no
                # downstream recipe step can consume it via template syntax.
                if cap_key == "local_bundle_path" and step_name == "export_local_bundle":
                    continue
                # Exempt stage-data resource_report captures: resource_report is the
                # path to the resource feasibility report in {{AUTOSKILLIT_TEMP}}/stage-data/.
                # It is captured for human-readable observability only — no downstream
                # recipe step consumes it via template syntax.
                if cap_key == "resource_report" and "stage-data" in step.with_args.get(
                    "skill_command", ""
                ):
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
) -> ValidationContext:
    """Build a ``ValidationContext`` from a recipe.

    Constructs the step graph and data-flow report once so that semantic
    rules can share the pre-built objects without redundant computation.
    """
    step_graph = _build_step_graph(recipe)
    dataflow = analyze_dataflow(recipe, step_graph=step_graph)
    return ValidationContext(
        recipe=recipe,
        step_graph=step_graph,
        dataflow=dataflow,
        available_recipes=available_recipes,
        available_skills=available_skills,
        available_sub_recipes=available_sub_recipes,
        project_dir=project_dir,
        disabled_subsets=disabled_subsets,
        blocks=extract_blocks(recipe, step_graph),
    )
