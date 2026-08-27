"""Semantic rules for gh pr merge silent-success and enrollment auto-consistency (R5, R8)."""

from __future__ import annotations

import regex as re

from autoskillit.core import Severity
from autoskillit.recipe._analysis import ValidationContext, bfs_reachable
from autoskillit.recipe.registry import RuleFinding, make_finding, semantic_rule

_AUTO_MERGE_FALSE_PATTERN = re.compile(
    r"auto_merge_available\s*==\s*['\"]?false['\"]?", re.IGNORECASE
)


def _is_auto_flagged_step(step_name: str, ctx: ValidationContext) -> bool:
    """Return True if step uses --auto in a gh pr merge command or calls toggle_auto_merge."""
    step = ctx.recipe.steps.get(step_name)
    if step is None:
        return False
    if step.tool == "run_cmd":
        cmd = step.with_args.get("cmd", "")
        if isinstance(cmd, str) and "gh pr merge" in cmd and "--auto" in cmd:
            return True
    if step.tool == "toggle_auto_merge":
        return True
    return False


@semantic_rule(
    name="gh-pr-merge-silent-success-routing",
    description=(
        "A run_cmd step that executes 'gh pr merge' must not route its on_failure "
        "to register_clone_success. A failed merge means the PR was NOT merged; routing "
        "to the success terminal silently reports the PR as done when it is not. "
        "Cleanup steps are exempt: steps with optional=True, or steps whose name starts "
        "with 'release_issue_' (all release_issue_* steps are terminal cleanup steps by "
        "convention — they never perform primary merge work)."
    ),
    severity=Severity.ERROR,
)
def _check_gh_pr_merge_silent_success_degradation(ctx: ValidationContext) -> list[RuleFinding]:
    findings: list[RuleFinding] = []
    for step_name, step in ctx.recipe.steps.items():
        if step.tool != "run_cmd":
            continue
        cmd = step.with_args.get("cmd", "")
        if not isinstance(cmd, str) or "gh pr merge" not in cmd:
            continue
        # Exempt cleanup steps: optional=True, or name starts with release_issue_
        # (release_issue_* steps are terminal cleanup steps by convention)
        if step.optional or step_name.startswith("release_issue_"):
            continue
        if step.on_failure == "register_clone_success":
            findings.append(
                make_finding(
                    rule_name="gh-pr-merge-silent-success-routing",
                    step_name=step_name,
                    message=(
                        f"Step '{step_name}' runs 'gh pr merge' but routes "
                        f"on_failure to 'register_clone_success' (a success terminal). "
                        f"A failed merge command means the PR was NOT merged. "
                        f"Route on_failure to an escalation target such as "
                        f"'release_issue_failure' or 'verify_queue_enrollment'."
                    ),
                )
            )
    return findings


@semantic_rule(
    name="merge-enrollment-auto-consistency",
    description=(
        "gh pr merge steps with --auto must not be reachable from auto_merge_available=false "
        "routing arms. When auto_merge_available is false, --auto and toggle_auto_merge will "
        "fail because the repository does not support enablePullRequestAutoMerge."
    ),
    severity=Severity.ERROR,
)
def _check_merge_enrollment_auto_consistency(ctx: ValidationContext) -> list[RuleFinding]:
    findings: list[RuleFinding] = []

    no_auto_targets: set[str] = set()
    for step_name, step in ctx.recipe.steps.items():
        if step.on_result and step.on_result.conditions:
            for cond in step.on_result.conditions:
                if cond.when and _AUTO_MERGE_FALSE_PATTERN.search(cond.when):
                    no_auto_targets.add(cond.route)

    for target in no_auto_targets:
        reachable = bfs_reachable(ctx.step_graph, target) | {target}
        for reached in reachable:
            if _is_auto_flagged_step(reached, ctx):
                findings.append(
                    make_finding(
                        rule_name="merge-enrollment-auto-consistency",
                        step_name=reached,
                        message=(
                            f"Step '{reached}' uses --auto or toggle_auto_merge but is "
                            f"reachable from an auto_merge_available=false routing arm "
                            f"(via '{target}'). Use enqueue_pr instead."
                        ),
                    )
                )
    return findings
