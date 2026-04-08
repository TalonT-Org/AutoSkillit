"""Semantic rules for CI polling patterns in recipe steps."""

from __future__ import annotations

from autoskillit.core import Severity
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


_CI_EVENT_SCOPE_TOOLS = {"wait_for_ci", "get_ci_status"}


@semantic_rule(
    name="ci-missing-event-scope",
    description="CI tool step without event parameter risks cross-event confusion",
    severity=Severity.WARNING,
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
                    severity=Severity.WARNING,
                    step_name=step_name,
                    message=(
                        f"Step '{step_name}' calls {step.tool} without an 'event' parameter. "
                        f"Without event filtering, a passing pull_request run can mask a "
                        f"failing push run. Add event: 'push' (or the appropriate trigger "
                        f"event) to the step's with_args, or set ci.event in project config."
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
