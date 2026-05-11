"""Semantic validation rules — graph/routing analysis."""

from __future__ import annotations

import regex as re

from autoskillit.core import (
    SKILL_TOOLS,
    Severity,
    get_logger,
    resolve_skill_name,
)
from autoskillit.recipe._analysis import ValidationContext
from autoskillit.recipe._analysis_bfs import bfs_reachable_without_barrier
from autoskillit.recipe.contracts import (
    _CONTEXT_REF_RE,
    get_tool_output_contract,
    load_bundled_manifest,
)
from autoskillit.recipe.registry import RuleFinding, semantic_rule

logger = get_logger(__name__)

_STRUCTURAL_ON_RESULT_TOOLS = {"run_python", "wait_for_ci"}


@semantic_rule(
    name="unbounded-cycle",
    description="Routing cycle with no structural termination guarantee",
    severity=Severity.ERROR,
)
def _check_unbounded_cycles(ctx: ValidationContext) -> list[RuleFinding]:
    recipe = ctx.recipe
    graph = ctx.step_graph
    findings: list[RuleFinding] = []
    reported_cycles: set[frozenset[str]] = set()

    visited: set[str] = set()
    rec_stack: set[str] = set()

    def dfs(node: str, path: list[str]) -> None:
        visited.add(node)
        rec_stack.add(node)
        for neighbor in sorted(graph.get(node, set())):
            if neighbor not in recipe.steps:
                continue  # dead reference — caught by validate_recipe_structure
            if neighbor not in visited:
                dfs(neighbor, path + [neighbor])
            elif neighbor in rec_stack:
                # Reconstruct the cycle steps from the path
                if neighbor in path:
                    cycle_steps = path[path.index(neighbor) :]
                else:
                    cycle_steps = path
                cycle_key = frozenset(cycle_steps)
                if cycle_key in reported_cycles:
                    rec_stack.discard(node)
                    return
                reported_cycles.add(cycle_key)
                cycle_set = set(cycle_steps)

                has_on_result_exit = False
                for s in cycle_steps:
                    if s not in recipe.steps:
                        continue
                    step = recipe.steps[s]
                    if step.tool not in _STRUCTURAL_ON_RESULT_TOOLS or step.on_result is None:
                        continue
                    targets: set[str] = set()
                    if step.on_result.conditions:
                        targets = {c.route for c in step.on_result.conditions}
                    elif step.on_result.routes:
                        targets = set(step.on_result.routes.values())
                    routes_out = targets - cycle_set
                    if routes_out:
                        has_on_result_exit = True
                        break

                if has_on_result_exit:
                    rec_stack.discard(node)
                    return

                has_retry_exit = any(
                    recipe.steps[s].retries > 0
                    and recipe.steps[s].tool in SKILL_TOOLS
                    and recipe.steps[s].on_exhausted not in cycle_set
                    for s in cycle_steps
                    if s in recipe.steps
                )
                if has_retry_exit:
                    # Check whether the success path of the retrying step stays inside
                    # the cycle. If it does, the retry exit only bounds individual visits
                    # but the outer loop can still iterate unboundedly.
                    retrying_steps = [
                        s
                        for s in cycle_steps
                        if s in recipe.steps
                        and recipe.steps[s].retries > 0
                        and recipe.steps[s].tool in SKILL_TOOLS
                        and recipe.steps[s].on_exhausted not in cycle_set
                    ]
                    # Check whether any non-failure successor of the retrying step
                    # stays within the cycle. Uses the step graph (which includes
                    # on_result routes) rather than on_success alone, so steps that
                    # route via on_result without an explicit on_success are handled.
                    success_stays_in_cycle = False
                    for _s in retrying_steps:
                        _step = recipe.steps[_s]
                        _fail_targets = {
                            t
                            for t in (
                                _step.on_failure,
                                _step.on_exhausted,
                                _step.on_context_limit,
                            )
                            if t
                        }
                        if any(
                            succ in cycle_set
                            for succ in graph.get(_s, set())
                            if succ not in _fail_targets
                        ):
                            success_stays_in_cycle = True
                            break
                    if not success_stays_in_cycle:
                        # Success path exits the cycle — but does it loop back?
                        # BFS from exit targets to check if they can reach any
                        # cycle member through the step graph.
                        exit_targets: set[str] = set()
                        for _rs in retrying_steps:
                            _step_r = recipe.steps[_rs]
                            _fail_targets_r = {
                                t
                                for t in (
                                    _step_r.on_failure,
                                    _step_r.on_exhausted,
                                    _step_r.on_context_limit,
                                )
                                if t
                            }
                            for succ in graph.get(_rs, set()):
                                if succ not in cycle_set and succ not in _fail_targets_r:
                                    exit_targets.add(succ)
                        loops_back = False
                        visited_exit: set[str] = set()
                        frontier = exit_targets
                        while frontier:
                            if frontier & cycle_set:
                                loops_back = True
                                break
                            visited_exit |= frontier
                            nxt: set[str] = set()
                            for f in frontier:
                                nxt |= set(graph.get(f, set())) - visited_exit
                            frontier = nxt
                        if not loops_back:
                            # Truly exits — cycle is bounded
                            rec_stack.discard(node)
                            return

                    # Success path re-enters the cycle — retry exit only bounds
                    # individual step visits, not the outer loop. Emit ERROR.
                    findings.append(
                        RuleFinding(
                            rule="unbounded-cycle",
                            severity=Severity.ERROR,
                            step_name=node,
                            message=(
                                f"Routing cycle detected: {' → '.join(cycle_steps)} → {neighbor}. "
                                f"Step(s) {', '.join(retrying_steps)} have retry exits, but their "
                                f"success paths re-enter the cycle. The inner retry budget resets "
                                f"on each loop iteration, so the outer loop is unbounded. "
                                "Add a check_loop_iteration guard step to enforce a hard "
                                "iteration cap, or route the success path outside the cycle."
                            ),
                        )
                    )
                    rec_stack.discard(node)
                    return

                # Conditional exit: on_failure pointing outside the cycle (unbounded but escapable)
                has_failure_exit = any(
                    recipe.steps[s].on_failure is not None
                    and recipe.steps[s].on_failure not in cycle_set
                    for s in cycle_steps
                    if s in recipe.steps
                )

                if has_failure_exit:
                    severity = Severity.WARNING
                    message = (
                        f"Routing cycle detected: {' → '.join(cycle_steps)} → {neighbor}. "
                        f"The cycle has a conditional exit path but no structural bound on "
                        f"iterations. Add 'retries: N' to at least one cycling step "
                        f"to enforce a maximum iteration count."
                    )
                else:
                    severity = Severity.ERROR
                    message = (
                        f"Routing cycle detected: {' → '.join(cycle_steps)} → {neighbor}. "
                        f"No step in this cycle has an exit edge — this cycle has no "
                        f"termination guarantee and will loop forever. Add 'retries: N' "
                        f"with on_exhausted outside the cycle, or route on_failure to a step "
                        f"outside the cycle."
                    )
                findings.append(
                    RuleFinding(
                        rule="unbounded-cycle",
                        severity=severity,
                        step_name=node,
                        message=message,
                    )
                )
        rec_stack.discard(node)

    for step_name in recipe.steps:
        if step_name not in visited:
            dfs(step_name, [step_name])

    return findings


@semantic_rule(
    name="on-result-missing-failure-route",
    description=(
        "All tool and python steps using on_result must declare on_failure. "
        "on_result (both legacy and predicate format) only fires when the tool "
        "succeeds and returns a recognized result. When the tool call itself fails "
        "(success: false), on_result never evaluates. on_failure is the required "
        "route for tool-level failures and must be declared on all steps."
    ),
    severity=Severity.ERROR,
)
def _check_on_result_missing_failure_route(ctx: ValidationContext) -> list[RuleFinding]:
    wf = ctx.recipe
    findings: list[RuleFinding] = []
    for step_name, step in wf.steps.items():
        is_tool_invocation = step.tool is not None or step.python is not None
        if not (is_tool_invocation and step.on_result is not None and step.on_failure is None):
            continue
        findings.append(
            RuleFinding(
                rule="on-result-missing-failure-route",
                severity=Severity.ERROR,
                step_name=step_name,
                message=(
                    f"Step '{step_name}' uses on_result but has no on_failure. "
                    f"If the tool call fails before a verdict is returned, the "
                    f"orchestrator has no route. Add on_failure: <target>."
                ),
            )
        )
    return findings


@semantic_rule(
    name="tool-step-missing-failure-route",
    description=(
        "All tool and python steps must declare on_failure. When a tool call "
        "returns success: false, the orchestrator needs a deterministic route. "
        "Without on_failure, failure handling is deferred to model improvisation."
    ),
    severity=Severity.ERROR,
)
def _check_tool_step_missing_failure_route(ctx: ValidationContext) -> list[RuleFinding]:
    wf = ctx.recipe
    findings: list[RuleFinding] = []
    for step_name, step in wf.steps.items():
        is_tool_invocation = step.tool is not None or step.python is not None
        if not (is_tool_invocation and step.on_failure is None):
            continue
        findings.append(
            RuleFinding(
                rule="tool-step-missing-failure-route",
                severity=Severity.ERROR,
                step_name=step_name,
                message=(
                    f"Step '{step_name}' invokes a tool/python callable but has no "
                    f"on_failure route. Add on_failure: <target> (use 'escalate' to "
                    f"abort the recipe on failure)."
                ),
            )
        )
    return findings


@semantic_rule(
    name="tool-step-missing-success-route",
    description=(
        "Tool and python steps should declare on_success or on_result. Without "
        "a success route, the orchestrator has no defined next step after a "
        "successful tool call."
    ),
    severity=Severity.WARNING,
)
def _check_tool_step_missing_success_route(ctx: ValidationContext) -> list[RuleFinding]:
    wf = ctx.recipe
    findings: list[RuleFinding] = []
    for step_name, step in wf.steps.items():
        is_tool_invocation = step.tool is not None or step.python is not None
        has_success_route = step.on_success is not None or step.on_result is not None
        if not (is_tool_invocation and not has_success_route):
            continue
        findings.append(
            RuleFinding(
                rule="tool-step-missing-success-route",
                severity=Severity.WARNING,
                step_name=step_name,
                message=(
                    f"Step '{step_name}' invokes a tool/python callable but has no "
                    f"success route (on_success or on_result). Add on_success: <target>."
                ),
            )
        )
    return findings


@semantic_rule(
    name="push-before-audit",
    description="push_to_remote reachable without passing through audit-impl first",
    severity=Severity.WARNING,
)
def _check_push_before_audit(ctx: ValidationContext) -> list[RuleFinding]:
    wf = ctx.recipe
    graph = ctx.step_graph
    push_steps = {name for name, step in wf.steps.items() if step.tool == "push_to_remote"}
    if not push_steps:
        return []

    audit_steps = {
        name
        for name, step in wf.steps.items()
        if step.tool in SKILL_TOOLS and "audit-impl" in step.with_args.get("skill_command", "")
    }

    entry = next(iter(wf.steps))

    reachable_without_audit: set[str] = set()
    queue = [entry]
    while queue:
        node = queue.pop()
        if node in reachable_without_audit:
            continue
        reachable_without_audit.add(node)
        if node in audit_steps:
            continue  # barrier: do not expand beyond the first audit step on this path
        for successor in graph.get(node, set()):
            if successor not in reachable_without_audit:
                queue.append(successor)

    violations = sorted(push_steps & reachable_without_audit)
    return [
        RuleFinding(
            rule="push-before-audit",
            severity=Severity.WARNING,
            step_name=name,
            message=(
                f"'{name}' uses push_to_remote but is reachable from the entry "
                "point without passing through an audit-impl skill step. "
                "Ensure audit-impl runs before any push_to_remote."
            ),
        )
        for name in violations
    ]


@semantic_rule(
    name="clone-root-as-worktree",
    description="worktree_path must not trace back to result.clone_path (the clone root)",
    severity=Severity.ERROR,
)
def _check_clone_root_as_worktree(ctx: ValidationContext) -> list[RuleFinding]:
    """Error when worktree_path for test_check/merge_worktree originates from clone_path.

    Builds a capture map by iterating recipe steps in declaration order.
    For each test_check or merge_worktree step, resolves the context variable
    used for worktree_path and checks whether it was captured from result.clone_path.
    """
    wf = ctx.recipe
    captures: dict[str, str] = {}  # var_name -> capture expression
    findings: list[RuleFinding] = []

    for step_name, step in wf.steps.items():
        if step.tool in ("test_check", "merge_worktree"):
            worktree_arg = step.with_args.get("worktree_path", "")
            if isinstance(worktree_arg, str):
                for var_name in _CONTEXT_REF_RE.findall(worktree_arg):
                    cap_expr = captures.get(var_name, "")
                    if "result.clone_path" in cap_expr:
                        findings.append(
                            RuleFinding(
                                rule="clone-root-as-worktree",
                                severity=Severity.ERROR,
                                step_name=step_name,
                                message=(
                                    f"Step '{step_name}' passes worktree_path via "
                                    f"'context.{var_name}', which was captured from "
                                    f"result.clone_path. clone_path is the root of the "
                                    f"cloned repository, not a git worktree. "
                                    f"Capture worktree_path from result.worktree_path "
                                    f"(e.g., from an implement-worktree step's capture block)."
                                ),
                            )
                        )

        # Update capture map AFTER the tool check so captures only affect later steps
        for cap_key, cap_val in step.capture.items():
            captures[cap_key] = str(cap_val)

    return findings


# ---------------------------------------------------------------------------
# merge-base-unpublished (ported from integration branch PR #81)
# ---------------------------------------------------------------------------

_CONTEXT_VAR_RE = re.compile(r"\$\{\{\s*context\.(\w+)\s*\}\}")


def _extract_context_var(value: str) -> str | None:
    """Return the context variable name from '${{ context.X }}', or None."""
    m = _CONTEXT_VAR_RE.fullmatch(value.strip())
    return m.group(1) if m else None


@semantic_rule(
    name="merge-base-unpublished",
    description=(
        "merge_worktree base_branch is a context variable without a preceding "
        "push_to_remote on all structural paths"
    ),
    severity=Severity.ERROR,
)
def _check_merge_base_unpublished(ctx: ValidationContext) -> list[RuleFinding]:
    """Fire when a merge_worktree step uses a context variable as base_branch
    and no push_to_remote step that pushes the same variable precedes it on
    all reachable paths in the raw structural routing graph.

    Uses the raw routing graph (without skip_when_false bypass edges) to avoid
    false positives when paired optional steps share the same skip_when_false
    condition (e.g., create_branch and push_merge_target both guarded by open_pr).

    Algorithm:
    1. Find all merge_worktree steps whose base_branch arg is ${{ context.X }}.
    2. For each, build a raw step graph (routing fields only, no bypass edges).
    3. Find push_to_remote steps whose branch arg references the same context.X.
    4. BFS from the recipe entry point treating push steps as barriers.
    5. If the merge step is reachable in this BFS, at least one path to it
       lacks a push barrier — fire the rule.
    """
    recipe = ctx.recipe
    if not recipe.steps:
        return []
    findings = []
    entry = next(iter(recipe.steps))
    step_names = set(recipe.steps.keys())

    # Build raw routing graph (no skip_when_false bypass edges).
    graph: dict[str, set[str]] = {name: set() for name in step_names}
    for name, step in recipe.steps.items():
        for target in (step.on_success, step.on_failure, step.on_context_limit):
            if target and target in step_names:
                graph[name].add(target)
        if step.on_result:
            for t in step.on_result.routes.values():
                if t in step_names:
                    graph[name].add(t)
            for cond in step.on_result.conditions:
                if cond.route in step_names:
                    graph[name].add(cond.route)
        if step.action is None and step.on_exhausted in step_names:
            graph[name].add(step.on_exhausted)

    for step_name, step in recipe.steps.items():
        if step.tool != "merge_worktree":
            continue
        base_branch_arg = (step.with_args or {}).get("base_branch", "")
        context_var = _extract_context_var(base_branch_arg)
        if context_var is None:
            continue  # literal branch name — always published, no check needed

        # Collect steps that publish this exact context variable.
        # push_to_remote steps are barriers when their branch arg is context.X.
        # create_and_publish_branch steps are barriers when they capture X (they
        # always push the created branch before capturing it as merge_target).
        push_steps = {
            name
            for name, s in recipe.steps.items()
            if (
                s.tool == "push_to_remote"
                and _extract_context_var((s.with_args or {}).get("branch", "")) == context_var
            )
            or (s.tool == "create_and_publish_branch" and context_var in (s.capture or {}))
        }

        # BFS from entry treating push_steps as barriers.
        # If step_name is reachable, some path lacks a push — fire the rule.
        visited: set[str] = set()
        queue = [entry]
        reachable_without_push = False
        while queue:
            node = queue.pop()
            if node in visited:
                continue
            visited.add(node)
            if node == step_name:
                reachable_without_push = True
                break
            if node in push_steps:
                continue  # barrier: do not expand through push
            queue.extend(graph.get(node, set()))

        if reachable_without_push:
            findings.append(
                RuleFinding(
                    rule="merge-base-unpublished",
                    severity=Severity.ERROR,
                    step_name=step_name,
                    message=(
                        f"Step '{step_name}' uses context.{context_var} as base_branch "
                        f"for merge_worktree, but no push_to_remote step that pushes "
                        f"context.{context_var} precedes it on all reachable paths. "
                        f"A locally-created branch must be published (push_to_remote) "
                        f"before merge_worktree can rebase against it."
                    ),
                )
            )

    return findings


@semantic_rule(
    name="on-result-missing-tool-output-value",
    description=(
        "Recoverable tool output values falling through to a terminal catch-all "
        "step. When a tool has declared recoverable_values in tool_output_contracts "
        "and none of them are explicitly routed in on_result conditions, a catch-all "
        "that routes to a terminal (action: stop) step silently drops those values."
    ),
    severity=Severity.WARNING,
)
def _check_on_result_missing_tool_output_value(ctx: ValidationContext) -> list[RuleFinding]:
    recipe = ctx.recipe
    findings: list[RuleFinding] = []
    for step_name, step in recipe.steps.items():
        if step.tool is None or step.on_result is None:
            continue
        contract = get_tool_output_contract(step.tool)
        if contract is None:
            continue
        field_def = contract.fields.get(contract.result_field)
        if field_def is None or not field_def.recoverable_values:
            continue
        conditions = step.on_result.conditions or []
        explicitly_routed: set[str] = set()
        catchall_route: str | None = None
        for cond in conditions:
            if cond.when is None:
                catchall_route = cond.route
            else:
                for val in field_def.allowed_values:
                    if val in cond.when:
                        explicitly_routed.add(val)
        if catchall_route is None:
            continue
        catchall_step = recipe.steps.get(catchall_route)
        if catchall_step is None or catchall_step.action != "stop":
            continue
        unrouted_recoverable = field_def.recoverable_values - explicitly_routed
        if not unrouted_recoverable:
            continue
        findings.append(
            RuleFinding(
                rule="on-result-missing-tool-output-value",
                severity=Severity.WARNING,
                step_name=step_name,
                message=(
                    f"Step '{step_name}' (tool: {step.tool}) has recoverable "
                    f"output values {sorted(unrouted_recoverable)} not explicitly "
                    f"routed in on_result. The catch-all routes to terminal step "
                    f"'{catchall_route}' (action: stop), silently terminating on "
                    f"these recoverable outcomes."
                ),
            )
        )
    return findings


@semantic_rule(
    name="skill-result-routing-gap",
    description=(
        "A run_skill step that captures a skill output with declared allowed_values "
        "must have an explicit on_result condition for every allowed value. "
        "If the output is not captured at all and the catch-all routes to a "
        "non-terminal step, unhandled values silently fall through to the success path."
    ),
    severity=Severity.ERROR,
)
def _check_skill_result_routing_gap(ctx: ValidationContext) -> list[RuleFinding]:
    findings: list[RuleFinding] = []
    try:
        manifest = load_bundled_manifest()
    except (FileNotFoundError, OSError, ValueError):
        logger.warning("failed to load bundled manifest", exc_info=True)
        return findings
    for step_name, step in ctx.recipe.steps.items():
        if step.tool != "run_skill":
            continue
        skill_command = (step.with_args or {}).get("skill_command", "")
        skill_name = resolve_skill_name(skill_command)
        if not skill_name:
            continue
        skill_contract = manifest.get("skills", {}).get(skill_name, {})
        outputs_with_allowed_values: dict[str, list[str]] = {}
        for output in skill_contract.get("outputs", []):
            if "allowed_values" in output:
                outputs_with_allowed_values[output["name"]] = output["allowed_values"]
        if not outputs_with_allowed_values:
            continue
        captured_outputs = set()
        if step.capture:
            for captured_var, capture_expr in step.capture.items():
                for output_name in outputs_with_allowed_values:
                    if f"result.{output_name}" in capture_expr:
                        captured_outputs.add(output_name)
        pass_through_set = set(step.pass_through)
        captured_outputs -= pass_through_set
        for pt_name in pass_through_set:
            outputs_with_allowed_values.pop(pt_name, None)
        if not step.on_result or not step.on_result.conditions:
            if captured_outputs:
                for output_name in captured_outputs:
                    findings.append(
                        RuleFinding(
                            rule="skill-result-routing-gap",
                            severity=Severity.ERROR,
                            step_name=step_name,
                            message=(
                                f"Step '{step_name}' captures '{output_name}' but has no "
                                f"on_result conditions to route its allowed values "
                                f"{outputs_with_allowed_values[output_name]}."
                            ),
                        )
                    )
            continue
        conditions = step.on_result.conditions or []
        for output_name, allowed_values in outputs_with_allowed_values.items():
            explicitly_routed: set[str] = set()
            catchall_route: str | None = None
            for cond in conditions:
                if cond.when is None:
                    catchall_route = cond.route
                else:
                    for val in allowed_values:
                        if val in cond.when:
                            explicitly_routed.add(val)
            unrouted = [v for v in allowed_values if v not in explicitly_routed]
            if not unrouted:
                continue
            if catchall_route is None:
                continue
            catchall_step = ctx.recipe.steps.get(catchall_route)
            is_terminal = catchall_step is not None and catchall_step.action == "stop"
            if is_terminal:
                continue
            findings.append(
                RuleFinding(
                    rule="skill-result-routing-gap",
                    severity=Severity.ERROR,
                    step_name=step_name,
                    message=(
                        f"Step '{step_name}' has allowed value(s) {unrouted} of "
                        f"'{output_name}' not explicitly routed in on_result. "
                        f"Catch-all routes to non-terminal step '{catchall_route}'."
                    ),
                )
            )
    return findings


@semantic_rule(
    name="pass-through-validity",
    description=(
        "A step's pass_through list must only reference outputs that are actually "
        "captured by the step, and must not reference outputs used in on_result "
        "when clauses (which indicates the output controls routing)."
    ),
    severity=Severity.WARNING,
)
def _check_pass_through_validity(ctx: ValidationContext) -> list[RuleFinding]:
    findings: list[RuleFinding] = []
    try:
        manifest = load_bundled_manifest()
    except (FileNotFoundError, OSError, ValueError):
        logger.warning("failed to load bundled manifest", exc_info=True)
        return findings
    for step_name, step in ctx.recipe.steps.items():
        if step.tool != "run_skill":
            continue
        if not step.pass_through:
            continue
        skill_command = (step.with_args or {}).get("skill_command", "")
        if not isinstance(skill_command, str):
            continue
        skill_name = resolve_skill_name(skill_command)
        if not skill_name:
            continue
        skill_contract = manifest.get("skills", {}).get(skill_name, {})
        all_output_names: set[str] = set()
        outputs_with_allowed_values: dict[str, list[str]] = {}
        for output in skill_contract.get("outputs", []):
            all_output_names.add(output["name"])
            if "allowed_values" in output:
                outputs_with_allowed_values[output["name"]] = output["allowed_values"]
        captured_outputs: set[str] = set()
        if step.capture:
            for captured_var, capture_expr in step.capture.items():
                for output_name in all_output_names:
                    if f"result.{output_name}" in capture_expr:
                        captured_outputs.add(output_name)
        used_in_when: set[str] = set()
        if step.on_result and step.on_result.conditions:
            for cond in step.on_result.conditions:
                if cond.when:
                    for output_name in outputs_with_allowed_values:
                        if f"result.{output_name}" in cond.when:
                            used_in_when.add(output_name)
        for pt_name in step.pass_through:
            if pt_name not in captured_outputs:
                findings.append(
                    RuleFinding(
                        rule="pass-through-validity",
                        severity=Severity.WARNING,
                        step_name=step_name,
                        message=(
                            f"pass_through references '{pt_name}' but this output "
                            f"is not captured by step '{step_name}'."
                        ),
                    )
                )
            elif pt_name in used_in_when:
                findings.append(
                    RuleFinding(
                        rule="pass-through-validity",
                        severity=Severity.WARNING,
                        step_name=step_name,
                        message=(
                            f"pass_through references '{pt_name}' but this output "
                            f"is used in a when clause of step '{step_name}' on_result, "
                            f"indicating it controls routing."
                        ),
                    )
                )
    return findings


@semantic_rule(
    name="review-loop-waypoint-guard",
    description=(
        "check_repo_ci_event must not be reachable from review_pr without "
        "traversing check_review_loop. Bypassing check_review_loop prevents "
        "review_loop_count from incrementing, causing review_mode to remain "
        "'local' permanently and no GitHub comments to be posted on clean PRs."
    ),
    severity=Severity.ERROR,
)
def _check_review_loop_waypoint(ctx: ValidationContext) -> list[RuleFinding]:
    steps = ctx.recipe.steps
    if not all(k in steps for k in ("review_pr", "check_review_loop", "check_repo_ci_event")):
        return []

    reachable = bfs_reachable_without_barrier(
        recipe=ctx.recipe,
        start="review_pr",
        barrier="check_review_loop",
    )

    if "check_repo_ci_event" not in reachable:
        return []

    return [
        RuleFinding(
            rule="review-loop-waypoint-guard",
            severity=Severity.ERROR,
            step_name="review_pr",
            message=(
                "check_repo_ci_event is reachable from review_pr without crossing "
                "check_review_loop. All review_pr verdicts must route through "
                "check_review_loop so review_loop_count is always incremented and "
                "review_mode can graduate from 'local' to 'github'."
            ),
        )
    ]


@semantic_rule(
    name="review-mode-reentry-waypoint-guard",
    description=(
        "review_pr must not be reachable from check_review_loop without "
        "traversing annotate_pr_diff. Bypassing annotate_pr_diff prevents "
        "review_mode from being recomputed on loop re-entry, causing mode "
        "to remain 'local' permanently and no GitHub comments to be posted."
    ),
    severity=Severity.ERROR,
)
def _check_review_mode_reentry_waypoint(ctx: ValidationContext) -> list[RuleFinding]:
    steps = ctx.recipe.steps
    if not all(k in steps for k in ("review_pr", "check_review_loop", "annotate_pr_diff")):
        return []

    reachable = bfs_reachable_without_barrier(
        recipe=ctx.recipe,
        start="check_review_loop",
        barrier="annotate_pr_diff",
    )

    if "review_pr" not in reachable:
        return []

    return [
        RuleFinding(
            rule="review-mode-reentry-waypoint-guard",
            severity=Severity.ERROR,
            step_name="check_review_loop",
            message=(
                "review_pr is reachable from check_review_loop without crossing "
                "annotate_pr_diff. All loop re-entry paths must traverse "
                "annotate_pr_diff so review_mode is recomputed with the updated "
                "review_loop_count on every iteration."
            ),
        )
    ]
