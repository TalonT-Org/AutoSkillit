"""Semantic rules for CI conflict gate routing and mergeability checks."""

from __future__ import annotations

from autoskillit.core import Severity
from autoskillit.recipe._analysis import ValidationContext
from autoskillit.recipe.registry import RuleFinding, semantic_rule

# ---------------------------------------------------------------------------
# ci-failure-missing-conflict-gate helpers
# ---------------------------------------------------------------------------

_CONFLICT_GATE_KEYWORDS: frozenset[str] = frozenset({"merge-base", "is-ancestor"})
_CONFLICT_RESOLUTION_SKILLS: frozenset[str] = frozenset({"resolve-merge-conflicts"})
_CODE_RESOLUTION_SKILLS: frozenset[str] = frozenset({"resolve-failures"})


def _is_exit_code_conflict_gate(step: object) -> bool:
    """Return True if step is a run_cmd stale-base exit-code gate (merge-base/is-ancestor)."""
    if getattr(step, "tool", None) != "run_cmd":
        return False
    cmd = (getattr(step, "with_args", {}) or {}).get("cmd", "")
    return isinstance(cmd, str) and any(kw in cmd for kw in _CONFLICT_GATE_KEYWORDS)


def _is_conflict_resolution_step(step: object) -> bool:
    """Return True if step invokes resolve-merge-conflicts via run_skill."""
    if getattr(step, "tool", None) != "run_skill":
        return False
    skill_cmd = (getattr(step, "with_args", {}) or {}).get("skill_command", "")
    return isinstance(skill_cmd, str) and any(s in skill_cmd for s in _CONFLICT_RESOLUTION_SKILLS)


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
        name
        for name, step in ctx.recipe.steps.items()
        if _is_exit_code_conflict_gate(step) or _is_conflict_resolution_step(step)
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
    name="mergeability-conflicting-direct-to-resolution",
    description=(
        "check_pr_mergeable CONFLICTING arm reaches a stale-base exit-code gate "
        "(run_cmd with merge-base) before reaching conflict resolution "
        "(resolve-merge-conflicts). Route CONFLICTING directly to resolution."
    ),
    severity=Severity.ERROR,
)
def _check_mergeability_conflicting_direct(ctx: ValidationContext) -> list[RuleFinding]:
    exit_code_gates: set[str] = {
        name for name, step in ctx.recipe.steps.items() if _is_exit_code_conflict_gate(step)
    }
    resolution_steps: set[str] = {
        name for name, step in ctx.recipe.steps.items() if _is_conflict_resolution_step(step)
    }
    if not exit_code_gates:
        return []
    findings: list[RuleFinding] = []
    for name, step in ctx.recipe.steps.items():
        if step.tool != "check_pr_mergeable":
            continue
        if step.on_result is None:
            continue
        for cond in step.on_result.conditions:
            if not cond.when or "CONFLICTING" not in cond.when:
                continue
            reachable = _bfs_without_barrier(ctx.step_graph, cond.route, resolution_steps)
            unguarded_gates = reachable & exit_code_gates
            if unguarded_gates:
                findings.append(
                    RuleFinding(
                        rule="mergeability-conflicting-direct-to-resolution",
                        severity=Severity.ERROR,
                        step_name=name,
                        message=(
                            f"Step '{name}' routes CONFLICTING to '{cond.route}' which "
                            f"reaches exit-code gate(s) "
                            f"({', '.join(sorted(unguarded_gates))}) "
                            f"before conflict resolution. GitHub's CONFLICTING status is "
                            f"authoritative — route directly to resolve-merge-conflicts."
                        ),
                    )
                )
    return findings


@semantic_rule(
    name="ci-conflict-path-missing-auto-trigger",
    description=(
        "A wait_for_ci step on a conflict-resolution path must have auto_trigger: true. "
        "After a force-push of a conflict fix, CI may not have been triggered yet — "
        "auto_trigger ensures the empty-commit CI trigger fires on the next poll cycle."
    ),
    severity=Severity.ERROR,
)
def _check_ci_conflict_path_missing_auto_trigger(ctx: ValidationContext) -> list[RuleFinding]:
    findings: list[RuleFinding] = []
    conflict_trigger_steps: set[str] = set()
    for step_name, step in ctx.recipe.steps.items():
        if not step.on_result or not step.on_result.conditions:
            continue
        for cond in step.on_result.conditions:
            if cond.when is not None and "CONFLICTING" in cond.when:
                conflict_trigger_steps.add(step_name)
                break
    for step_name, step in ctx.recipe.steps.items():
        if step.tool != "wait_for_ci":
            continue
        is_on_conflict_path = (
            "conflict" in step_name.lower()
            or step_name in conflict_trigger_steps
            or _has_conflict_ancestor(step_name, conflict_trigger_steps, ctx)
        )
        if not is_on_conflict_path:
            continue
        auto_trigger = (step.with_args or {}).get("auto_trigger", "")
        if str(auto_trigger).lower() != "true":
            findings.append(
                RuleFinding(
                    rule="ci-conflict-path-missing-auto-trigger",
                    severity=Severity.ERROR,
                    step_name=step_name,
                    message=(
                        f"Step '{step_name}' is on a conflict-resolution path but lacks "
                        f"auto_trigger: 'true' in with_args. After a conflict fix push, "
                        f"CI may not have fired yet — auto_trigger is mandatory here."
                    ),
                )
            )
    return findings


def _has_conflict_ancestor(
    step_name: str, conflict_steps: set[str], ctx: ValidationContext
) -> bool:
    from collections import deque

    visited: set[str] = set()
    queue = deque(ctx.predecessors.get(step_name, set()))
    while queue:
        current = queue.popleft()
        if current in visited:
            continue
        visited.add(current)
        if current in conflict_steps:
            return True
        queue.extend(ctx.predecessors.get(current, set()))
    return False
