"""Semantic validation rules — graph/routing analysis."""

from __future__ import annotations

from autoskillit.core import (
    SKILL_TOOLS,
    Severity,
    get_logger,
)
from autoskillit.recipe._analysis import _build_step_graph
from autoskillit.recipe.contracts import _CONTEXT_REF_RE
from autoskillit.recipe.registry import RuleFinding, semantic_rule
from autoskillit.recipe.schema import Recipe

logger = get_logger(__name__)


@semantic_rule(
    name="unbounded-cycle",
    description="Routing cycle with no structural termination guarantee",
    severity=Severity.ERROR,
)
def _check_unbounded_cycles(recipe: Recipe) -> list[RuleFinding]:
    graph = _build_step_graph(recipe)
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

                # Structural exit: retry.max_attempts > 0 with on_exhausted outside cycle
                # (a retry block with max_attempts is a structural bound — no finding)
                has_retry_exit = any(
                    (r := recipe.steps[s].retry) is not None
                    and r.max_attempts > 0
                    and r.on_exhausted not in cycle_set
                    for s in cycle_steps
                    if s in recipe.steps
                )
                if has_retry_exit:
                    # Structurally bounded — no finding
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
                        f"iterations. Add retry.max_attempts to at least one cycling step "
                        f"to enforce a maximum iteration count."
                    )
                else:
                    severity = Severity.ERROR
                    message = (
                        f"Routing cycle detected: {' → '.join(cycle_steps)} → {neighbor}. "
                        f"No step in this cycle has an exit edge — this cycle has no "
                        f"termination guarantee and will loop forever. Add retry.max_attempts "
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
        "Tool and python steps using on_result must also declare on_failure. "
        "on_result only fires when the tool succeeds and returns a recognized verdict. "
        "When the tool call itself fails (success: false), on_result never evaluates "
        "and the orchestrator has no route without on_failure."
    ),
    severity=Severity.ERROR,
)
def _check_on_result_missing_failure_route(wf: Recipe) -> list[RuleFinding]:
    findings: list[RuleFinding] = []
    for step_name, step in wf.steps.items():
        is_tool_invocation = step.tool is not None or step.python is not None
        if not (is_tool_invocation and step.on_result is not None and step.on_failure is None):
            continue
        # Predicate format: conditions encode all routing paths including failure.
        # on_failure is neither required nor expected for predicate-format steps.
        if step.on_result.conditions:
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
def _check_push_before_audit(wf: Recipe) -> list[RuleFinding]:
    push_steps = {name for name, step in wf.steps.items() if step.tool == "push_to_remote"}
    if not push_steps:
        return []

    audit_steps = {
        name
        for name, step in wf.steps.items()
        if step.tool in SKILL_TOOLS and "audit-impl" in step.with_args.get("skill_command", "")
    }

    graph = _build_step_graph(wf)
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
def _check_clone_root_as_worktree(wf: Recipe) -> list[RuleFinding]:
    """Error when worktree_path for test_check/merge_worktree originates from clone_path.

    Builds a capture map by iterating recipe steps in declaration order.
    For each test_check or merge_worktree step, resolves the context variable
    used for worktree_path and checks whether it was captured from result.clone_path.
    """
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
