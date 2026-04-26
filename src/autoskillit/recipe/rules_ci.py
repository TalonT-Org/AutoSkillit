"""Semantic rules for CI polling patterns in recipe steps."""

from __future__ import annotations

import re

from autoskillit.core import PRState, Severity
from autoskillit.recipe._analysis import ValidationContext
from autoskillit.recipe.registry import RuleFinding, semantic_rule


@semantic_rule(
    name="ci-polling-inline-shell",
    description="Flags run_cmd steps containing gh run commands — use wait_for_ci tool instead",
    severity=Severity.WARNING,
)
def _check_ci_polling_inline_shell(ctx: ValidationContext) -> list[RuleFinding]:
    findings: list[RuleFinding] = []
    for name, step in ctx.recipe.steps.items():
        if step.tool != "run_cmd":
            continue
        cmd = (step.with_args or {}).get("cmd", "")
        if not isinstance(cmd, str):
            continue
        if "gh run watch" in cmd or "gh run list" in cmd:
            findings.append(
                RuleFinding(
                    rule="ci-polling-inline-shell",
                    severity=Severity.WARNING,
                    step_name=name,
                    message=(
                        f"Step '{name}' uses inline 'gh run' commands in run_cmd. "
                        "Use the wait_for_ci MCP tool instead for race-immune CI watching "
                        "with structured output."
                    ),
                )
            )
    return findings


# ---------------------------------------------------------------------------
# ci-failure-missing-conflict-gate helpers
# ---------------------------------------------------------------------------

_CONFLICT_GATE_KEYWORDS: frozenset[str] = frozenset({"merge-base", "is-ancestor"})
_CONFLICT_RESOLUTION_SKILLS: frozenset[str] = frozenset({"resolve-merge-conflicts"})
_CODE_RESOLUTION_SKILLS: frozenset[str] = frozenset({"resolve-failures"})


def _is_conflict_gate_step(step: object) -> bool:
    """Return True if step is a stale-base conflict-detection gate.

    A gate is either:
    - run_cmd with a 'cmd' containing 'merge-base' or 'is-ancestor'
    - run_skill with a skill_command referencing 'resolve-merge-conflicts'
    """
    tool = getattr(step, "tool", None)
    with_args = getattr(step, "with_args", {}) or {}
    if tool == "run_cmd":
        cmd = with_args.get("cmd", "")
        return isinstance(cmd, str) and any(kw in cmd for kw in _CONFLICT_GATE_KEYWORDS)
    if tool == "run_skill":
        skill_cmd = with_args.get("skill_command", "")
        return isinstance(skill_cmd, str) and any(
            s in skill_cmd for s in _CONFLICT_RESOLUTION_SKILLS
        )
    return False


def _is_code_resolution_step(step: object) -> bool:
    """Return True if step invokes code-level CI resolution (resolve-failures)."""
    if getattr(step, "tool", None) != "run_skill":
        return False
    with_args = getattr(step, "with_args", {}) or {}
    skill_cmd = with_args.get("skill_command", "")
    return isinstance(skill_cmd, str) and any(s in skill_cmd for s in _CODE_RESOLUTION_SKILLS)


def _bfs_without_barrier(graph: dict[str, set[str]], start: str, barriers: set[str]) -> set[str]:
    """BFS from start; barrier nodes are visited but not expanded."""
    reachable: set[str] = set()
    queue = [start]
    while queue:
        node = queue.pop()
        if node in reachable:
            continue
        reachable.add(node)
        if node in barriers:
            continue
        for successor in graph.get(node, set()):
            if successor not in reachable:
                queue.append(successor)
    return reachable


@semantic_rule(
    name="ci-failure-missing-conflict-gate",
    description=(
        "wait_for_ci failure route reaches resolve-failures without a stale-base "
        "detection gate (run_cmd merge-base check or resolve-merge-conflicts)"
    ),
    severity=Severity.ERROR,
)
def _check_ci_failure_conflict_gate(ctx: ValidationContext) -> list[RuleFinding]:
    # Identify all conflict-gate and code-resolution steps by name
    conflict_gates: set[str] = {
        name for name, step in ctx.recipe.steps.items() if _is_conflict_gate_step(step)
    }
    code_resolution_steps: set[str] = {
        name for name, step in ctx.recipe.steps.items() if _is_code_resolution_step(step)
    }

    # If no automated code-resolution loop exists, skip (merge-prs.yaml pattern)
    if not code_resolution_steps:
        return []

    findings: list[RuleFinding] = []
    for name, step in ctx.recipe.steps.items():
        if step.tool != "wait_for_ci":
            continue
        failure_target = step.on_failure
        if failure_target is None:
            continue

        # BFS from the failure target; conflict-gate steps are barriers
        reachable = _bfs_without_barrier(ctx.step_graph, failure_target, conflict_gates)

        # If any code-resolution step is reachable before a conflict gate → violation
        unguarded = reachable & code_resolution_steps
        if unguarded:
            findings.append(
                RuleFinding(
                    rule="ci-failure-missing-conflict-gate",
                    severity=Severity.ERROR,
                    step_name=name,
                    message=(
                        f"Step '{name}' routes CI failures to code-level resolution "
                        f"({', '.join(sorted(unguarded))}) without a stale-base detection gate. "
                        "Insert a run_cmd step using 'git merge-base --is-ancestor' (or a "
                        "resolve-merge-conflicts skill step) before any resolve-failures "
                        "invocation on the CI failure path."
                    ),
                )
            )
    return findings


@semantic_rule(
    name="ci-no-runs-unguarded",
    description=(
        "Flags wait_for_ci steps that use bare on_success routing without "
        "on_result conditions that intercept conclusion='no_runs'"
    ),
    severity=Severity.ERROR,
)
def _check_ci_no_runs_unguarded(ctx: ValidationContext) -> list[RuleFinding]:
    findings: list[RuleFinding] = []
    for name, step in ctx.recipe.steps.items():
        if step.tool != "wait_for_ci":
            continue
        if step.on_result and step.on_result.conditions:
            has_no_runs_guard = any(
                c.when and "no_runs" in c.when for c in step.on_result.conditions
            )
            if has_no_runs_guard:
                continue
        if step.on_success:
            findings.append(
                RuleFinding(
                    rule="ci-no-runs-unguarded",
                    severity=Severity.ERROR,
                    step_name=name,
                    message=(
                        f"Step '{name}' uses wait_for_ci with bare on_success routing. "
                        "wait_for_ci returns conclusion='no_runs' on the success path — "
                        "add on_result conditions to intercept no_runs before routing "
                        f"to '{step.on_success}'."
                    ),
                )
            )
    return findings


_CI_EVENT_SCOPE_TOOLS = {"wait_for_ci", "get_ci_status"}


@semantic_rule(
    name="ci-missing-event-scope",
    description=(
        "CI tool step without event parameter causes silent run exclusion on feature branches"
    ),
    severity=Severity.ERROR,
)
def _check_ci_missing_event_scope(ctx: ValidationContext) -> list[RuleFinding]:
    findings: list[RuleFinding] = []
    for step_name, step in ctx.recipe.steps.items():
        if step.tool not in _CI_EVENT_SCOPE_TOOLS:
            continue
        if "event" not in (step.with_args or {}):
            findings.append(
                RuleFinding(
                    rule="ci-missing-event-scope",
                    severity=Severity.ERROR,
                    step_name=step_name,
                    message=(
                        f"Step '{step_name}' calls {step.tool} without an 'event' parameter. "
                        f"On feature branches excluded from push triggers, the push-scoped "
                        f"filter returns no runs even when pull_request CI is active, causing "
                        f"no_runs timeout. Add event: '${{{{ context.ci_event }}}}' "
                        f"(requires check_repo_ci_event to run first) "
                        f"or set ci.event in project config."
                    ),
                )
            )
    return findings


@semantic_rule(
    name="ci-hardcoded-workflow",
    description="wait_for_ci step with hardcoded workflow bypasses config fallback",
    severity=Severity.WARNING,
)
def _check_ci_hardcoded_workflow(ctx: ValidationContext) -> list[RuleFinding]:
    findings: list[RuleFinding] = []
    for step_name, step in ctx.recipe.steps.items():
        if step.tool != "wait_for_ci":
            continue
        workflow = (step.with_args or {}).get("workflow")
        if isinstance(workflow, str) and not workflow.startswith("${{"):
            findings.append(
                RuleFinding(
                    rule="ci-hardcoded-workflow",
                    severity=Severity.WARNING,
                    step_name=step_name,
                    message=(
                        f"Step '{step_name}' hardcodes workflow: '{workflow}'. "
                        f"Remove the workflow parameter to use the project-level "
                        f"ci.workflow config default, or use '${{{{ inputs.workflow }}}}' "
                        f"to parameterize it via recipe ingredients."
                    ),
                )
            )
    return findings


# ---------------------------------------------------------------------------
# wait_for_merge_queue routing rules (I7 + I8)
# ---------------------------------------------------------------------------

_REQUIRED_MQ_PR_STATES: frozenset[str] = frozenset(
    s.value for s in PRState if s not in {PRState.ERROR, PRState.NOT_ENROLLED}
)
_PR_STATE_WHEN_RE = re.compile(r"\$\{\{\s*result\.pr_state\s*\}\}\s*==\s*(\w+)")
_MQ_EXPECTED_FALLBACK = "register_clone_unconfirmed"


def _extract_mq_when_values(on_result: object) -> set[str]:
    """Return the set of pr_state values covered by explicit when conditions."""
    values: set[str] = set()
    for cond in getattr(on_result, "conditions", []):
        if getattr(cond, "when", None) is None:
            continue
        m = _PR_STATE_WHEN_RE.search(cond.when)
        if m:
            values.add(m.group(1))
    return values


def _recipe_has_mq_routing_step(ctx: ValidationContext) -> bool:
    """Return True if any step uses wait_for_merge_queue with predicate on_result routing."""
    return any(
        step.tool == "wait_for_merge_queue"
        and step.on_result is not None
        and getattr(step.on_result, "conditions", None)
        for step in ctx.recipe.steps.values()
    )


def _recipe_uses_register_clone_unconfirmed(ctx: ValidationContext) -> bool:
    """Return True if this recipe family uses register_clone_unconfirmed as timeout escalation.

    Used only by Rule I8 (conformance targets) — implementation/remediation-family recipes
    that define register_clone_unconfirmed must route fallback and on_failure there.  Other
    recipe families (e.g. merge-prs.yaml) route queue timeouts/errors differently and are
    exempt from target-specific conformance checks, but NOT from PRState completeness (I7).
    """
    return _MQ_EXPECTED_FALLBACK in ctx.recipe.steps


@semantic_rule(
    name="wait-for-merge-queue-routing-covers-all-pr-states",
    description=(
        "Every non-error PRState value must have an explicit when arm in "
        "wait_for_merge_queue on_result; prevents silent routing of new states to fallback"
    ),
    severity=Severity.ERROR,
)
def _check_wait_for_merge_queue_routing_covers_all_pr_states(
    ctx: ValidationContext,
) -> list[RuleFinding]:
    if not _recipe_has_mq_routing_step(ctx):
        return []
    findings: list[RuleFinding] = []
    for step_name, step in ctx.recipe.steps.items():
        if step.tool != "wait_for_merge_queue":
            continue
        if step.on_result is None or not step.on_result.conditions:
            continue
        covered = _extract_mq_when_values(step.on_result)
        missing = _REQUIRED_MQ_PR_STATES - covered
        if missing:
            findings.append(
                RuleFinding(
                    rule="wait-for-merge-queue-routing-covers-all-pr-states",
                    severity=Severity.ERROR,
                    step_name=step_name,
                    message=(
                        f"Step {step_name!r} is missing explicit routing arms for "
                        f"PRState values: {sorted(missing)}. Every non-error PRState "
                        f"must have an explicit when condition in on_result."
                    ),
                )
            )
    return findings


@semantic_rule(
    name="wait-for-merge-queue-routing-conforms-to-expected-targets",
    description=(
        "wait_for_merge_queue fallback and on_failure must both target "
        "register_clone_unconfirmed; prevents silent success routing on timeout/unknown states"
    ),
    severity=Severity.ERROR,
)
def _check_wait_for_merge_queue_routing_conforms_to_expected_targets(
    ctx: ValidationContext,
) -> list[RuleFinding]:
    if not _recipe_uses_register_clone_unconfirmed(ctx):
        return []
    findings: list[RuleFinding] = []
    for step_name, step in ctx.recipe.steps.items():
        if step.tool != "wait_for_merge_queue":
            continue
        # Check fallback condition (when=None)
        if step.on_result is not None and step.on_result.conditions:
            fallback_routes = [
                c.route for c in step.on_result.conditions if getattr(c, "when", None) is None
            ]
            for route in fallback_routes:
                if route != _MQ_EXPECTED_FALLBACK:
                    findings.append(
                        RuleFinding(
                            rule="wait-for-merge-queue-routing-conforms-to-expected-targets",
                            severity=Severity.ERROR,
                            step_name=step_name,
                            message=(
                                f"Step {step_name!r} has fallback route {route!r} but "
                                f"expected {_MQ_EXPECTED_FALLBACK!r}. The fallback must "
                                f"route to register_clone_unconfirmed so unrecognised states "
                                f"are escalated, not silently treated as success."
                            ),
                        )
                    )
        # Check on_failure
        if step.on_failure is not None and step.on_failure != _MQ_EXPECTED_FALLBACK:
            findings.append(
                RuleFinding(
                    rule="wait-for-merge-queue-routing-conforms-to-expected-targets",
                    severity=Severity.ERROR,
                    step_name=step_name,
                    message=(
                        f"Step {step_name!r} has on_failure={step.on_failure!r} but "
                        f"expected {_MQ_EXPECTED_FALLBACK!r}. Tool errors must route "
                        f"to register_clone_unconfirmed, not a success-path step."
                    ),
                )
            )
    return findings
