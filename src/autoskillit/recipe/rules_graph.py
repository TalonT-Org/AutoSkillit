"""Semantic validation rules — graph/routing analysis."""

from __future__ import annotations

import re

from autoskillit.core import (
    SKILL_TOOLS,
    Severity,
    get_logger,
)
from autoskillit.recipe._analysis import ValidationContext
from autoskillit.recipe.contracts import _CONTEXT_REF_RE
from autoskillit.recipe.registry import RuleFinding, semantic_rule

logger = get_logger(__name__)


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
        for neighbor in graph.get(node, set()):
            if neighbor not in recipe.steps:
                continue  # dead reference — caught by validate_recipe
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
                    # individual step visits, not the outer loop. Emit WARNING.
                    findings.append(
                        RuleFinding(
                            rule="unbounded-cycle",
                            severity=Severity.WARNING,
                            step_name=node,
                            message=(
                                f"Routing cycle detected: {' → '.join(cycle_steps)} → {neighbor}. "
                                f"Step(s) {', '.join(retrying_steps)} have retry exits, but their "
                                f"success paths re-enter the cycle. The inner retry budget resets "
                                f"on each loop iteration, so the outer loop is unbounded. "
                                "Add a global iteration counter or route success outside the "
                                "cycle."
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

        # Collect push_to_remote steps that push this exact context variable.
        push_steps = {
            name
            for name, s in recipe.steps.items()
            if s.tool == "push_to_remote"
            and _extract_context_var((s.with_args or {}).get("branch", "")) == context_var
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
