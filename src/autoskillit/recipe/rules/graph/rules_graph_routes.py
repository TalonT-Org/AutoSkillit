"""Semantic rules for route completeness and structural ordering."""

from __future__ import annotations

from autoskillit.core import SKILL_TOOLS, Severity
from autoskillit.recipe._analysis import ValidationContext
from autoskillit.recipe.contracts import _CONTEXT_REF_RE
from autoskillit.recipe.registry import RuleFinding, semantic_rule


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
    name="rate-limit-route-missing",
    description=(
        "run_skill steps with on_context_limit should also define on_rate_limit "
        "so transient 429 rate limits route differently from structural context exhaustion."
    ),
    severity=Severity.WARNING,
)
def _check_rate_limit_route_missing(ctx: ValidationContext) -> list[RuleFinding]:
    wf = ctx.recipe
    findings: list[RuleFinding] = []
    for step_name, step in wf.steps.items():
        if step.tool not in SKILL_TOOLS:
            continue
        if step.on_context_limit is not None and step.on_rate_limit is None:
            findings.append(
                RuleFinding(
                    rule="rate-limit-route-missing",
                    severity=Severity.WARNING,
                    step_name=step_name,
                    message=(
                        f"Step '{step_name}' defines on_context_limit but not "
                        f"on_rate_limit. Transient 429 rate limits will fall back "
                        f"to on_context_limit routing. Add on_rate_limit: <target> "
                        f"to handle rate limits explicitly."
                    ),
                )
            )
    return findings


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
