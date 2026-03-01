"""Recipe validation — structural, semantic rules, and dataflow analysis."""

from __future__ import annotations

from autoskillit.core import (
    RETRY_RESPONSE_FIELDS,
    SKILL_TOOLS,
    get_logger,
)
from autoskillit.recipe.contracts import (
    _CONTEXT_REF_RE,
    _INPUT_REF_RE,
    _RESULT_CAPTURE_RE,
    _TEMPLATE_REF_RE,
)
from autoskillit.recipe.io import iter_steps_with_context
from autoskillit.recipe.registry import (
    _RULE_REGISTRY,
    RuleFinding,
    RuleSpec,
    build_quality_dict,
    compute_recipe_validity,
    filter_version_rule,
    findings_to_dicts,
    run_semantic_rules,
    semantic_rule,
)
from autoskillit.recipe.schema import DataFlowReport, DataFlowWarning, Recipe

logger = get_logger(__name__)

# Re-export registry symbols so existing ``from autoskillit.recipe.validator import X``
# imports continue to work without modification.
__all__ = [
    "RuleFinding",
    "RuleSpec",
    "_RULE_REGISTRY",
    "build_quality_dict",
    "compute_recipe_validity",
    "filter_version_rule",
    "findings_to_dicts",
    "run_semantic_rules",
    "semantic_rule",
]


# ---------------------------------------------------------------------------
# Structural validation
# ---------------------------------------------------------------------------


def validate_recipe(recipe: Recipe) -> list[str]:
    """Return a list of validation errors (empty if valid)."""
    errors: list[str] = []

    if not recipe.name:
        errors.append("Recipe must have a 'name'.")
    if not recipe.steps:
        errors.append("Recipe must have at least one step.")

    step_names = set(recipe.steps.keys())

    for step_name, step in recipe.steps.items():
        discriminators = [d for d in ("tool", "action", "python") if getattr(step, d) is not None]
        if len(discriminators) == 0:
            errors.append(f"Step '{step_name}' must have 'tool', 'action', or 'python'.")
        if len(discriminators) > 1:
            errors.append(
                f"Step '{step_name}' has multiple discriminators "
                f"({', '.join(discriminators)}); pick one."
            )
        if step.python is not None and "." not in step.python:
            errors.append(
                f"Step '{step_name}'.python must be a dotted path "
                f"(module.function), got '{step.python}'."
            )
        if step.action == "stop" and not step.message:
            errors.append(f"Terminal step '{step_name}' (action: stop) must have a 'message'.")
        for goto_field in ("on_success", "on_failure", "on_retry"):
            target = getattr(step, goto_field)
            if target and target not in step_names and target != "done":
                errors.append(
                    f"Step '{step_name}'.{goto_field} references unknown step '{target}'."
                )
        if step.on_retry is not None and step.retry is not None and step.retry.on == "needs_retry":
            errors.append(
                f"Step '{step_name}' has both 'on_retry' and 'retry.on=\"needs_retry\"'; "
                f"they are mutually exclusive. Use 'on_retry' to route to a different step "
                f"on needs_retry=True, or 'retry' to re-run the same step — not both."
            )
        if step.retry and step.retry.on_exhausted not in step_names:
            errors.append(
                f"Step '{step_name}'.retry.on_exhausted references "
                f"unknown step '{step.retry.on_exhausted}'."
            )
        if step.retry and step.retry.on and step.retry.on not in RETRY_RESPONSE_FIELDS:
            errors.append(
                f"Step '{step_name}'.retry.on references unknown response field "
                f"'{step.retry.on}'. Valid fields: {sorted(RETRY_RESPONSE_FIELDS)}"
            )
        if step.on_result is not None:
            if step.on_success is not None:
                errors.append(
                    f"Step '{step_name}' has both 'on_result' and 'on_success'; "
                    f"they are mutually exclusive."
                )
            if step.on_result.conditions:
                # Predicate format validation
                if step.on_failure is not None:
                    errors.append(
                        f"Step '{step_name}' has both 'on_result' (predicate format) and "
                        f"'on_failure'; they are mutually exclusive. Predicate conditions "
                        f"handle all routing paths including failures."
                    )
                for i, cond in enumerate(step.on_result.conditions):
                    if not cond.route:
                        errors.append(
                            f"Step '{step_name}'.on_result[{i}].route must be non-empty."
                        )
                    elif cond.route not in step_names and cond.route != "done":
                        errors.append(
                            f"Step '{step_name}'.on_result[{i}].route references "
                            f"unknown step '{cond.route}'."
                        )
            else:
                # Legacy format validation
                if not step.on_result.field:
                    errors.append(f"Step '{step_name}'.on_result.field must be non-empty.")
                if not step.on_result.routes:
                    errors.append(f"Step '{step_name}'.on_result.routes must be non-empty.")
                for value, target in step.on_result.routes.items():
                    if target not in step_names and target != "done":
                        errors.append(
                            f"Step '{step_name}'.on_result.routes.{value} references "
                            f"unknown step '{target}'."
                        )

    # Validate capture values: must contain ${{ result.* }} expressions
    for step_name, step in recipe.steps.items():
        for cap_key, cap_val in step.capture.items():
            all_refs = _TEMPLATE_REF_RE.findall(cap_val)
            if not all_refs:
                errors.append(
                    f"Step '{step_name}'.capture.{cap_key} must contain "
                    f"a ${{{{ result.* }}}} expression."
                )
            for ref_match in all_refs:
                inner = ref_match[3:-2].strip()
                if not inner.startswith("result."):
                    errors.append(
                        f"Step '{step_name}'.capture.{cap_key} references "
                        f"'{inner}'; capture values must use the 'result.' namespace."
                    )

    # Validate input and context references in with_args using iter_steps_with_context
    ingredient_names = set(recipe.ingredients.keys())

    for step_name, step, available_context in iter_steps_with_context(recipe):
        for arg_key, arg_val in step.with_args.items():
            if not isinstance(arg_val, str):
                continue
            for ref in _INPUT_REF_RE.findall(arg_val):
                if ref not in ingredient_names:
                    errors.append(
                        f"Step '{step_name}'.with.{arg_key} references undeclared input '{ref}'."
                    )
            for ref in _CONTEXT_REF_RE.findall(arg_val):
                if ref not in available_context:
                    errors.append(
                        f"Step '{step_name}'.with.{arg_key} references "
                        f"context variable '{ref}' which has not been "
                        f"captured by a preceding step."
                    )

    if not recipe.kitchen_rules:
        errors.append(
            "Recipe has no 'kitchen_rules' field. Recipes should include "
            "orchestrator discipline constraints."
        )

    return errors


# ---------------------------------------------------------------------------
# Data-flow analysis
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
        # on_result bypass: predecessor → each on_result route target
        if step.on_result:
            for target in step.on_result.routes.values():
                if target in step_names:
                    for pred in predecessors[name]:
                        graph[pred].add(target)

    return graph


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


def _detect_ref_invalidations(recipe: Recipe, graph: dict[str, set[str]]) -> list[DataFlowWarning]:
    """Detect context variables consumed after the step that invalidated the
    underlying resource.

    The resource lifecycle contract:
    - ``merge_worktree`` on SUCCESS destroys the worktree directory and branch ref.
    - ``remove_clone`` on SUCCESS destroys the clone directory.

    Only steps reachable via ``on_success`` from the invalidating step are checked.
    Steps that re-capture the same variable are treated as barriers — they refresh
    the variable to a new (valid) resource, so their successors are excluded from
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

        # on_result routing on a captured key is structural consumption
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


# ---------------------------------------------------------------------------
# Semantic rules
# ---------------------------------------------------------------------------

# Import rules modules to trigger @semantic_rule registration and re-expose symbols.
from autoskillit.recipe import rules as _rules_module  # noqa: E402 F401
from autoskillit.recipe import rules_bypass as _rules_bypass_module  # noqa: E402 F401
from autoskillit.recipe.rules import (  # noqa: E402
    _WORKTREE_CREATING_SKILLS,  # noqa: F401 — test access
    _check_outdated_version,  # noqa: F401 — test access
)
