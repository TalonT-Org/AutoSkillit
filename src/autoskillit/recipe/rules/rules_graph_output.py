"""Semantic rules for merge-base ordering and output value routing."""

from __future__ import annotations

import regex as re

from autoskillit.core import Severity, get_logger, resolve_skill_name
from autoskillit.recipe._analysis import ValidationContext
from autoskillit.recipe.contracts import get_tool_output_contract, load_bundled_manifest
from autoskillit.recipe.registry import RuleFinding, semantic_rule

logger = get_logger(__name__)

_CONTEXT_VAR_RE = re.compile(r"\$\{\{\s*context\.(\w+)\s*\}\}")


def _extract_context_var(value: str) -> str | None:
    """Return the context variable name from '${{ context.X }}', or None (whole-string match)."""
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
        for target in (
            step.on_success,
            step.on_failure,
            step.on_context_limit,
            step.on_rate_limit,
        ):
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
