"""Semantic rules for merge_worktree routing completeness."""

from __future__ import annotations

import re

from autoskillit.core import MergeFailedStep, Severity, get_logger
from autoskillit.recipe._analysis import ValidationContext, _bfs_reachable
from autoskillit.recipe.registry import RuleFinding, semantic_rule

logger = get_logger(__name__)


def _is_commit_guard(step_name: str, ctx: ValidationContext) -> bool:
    """Return True if step_name is a commit_guard predecessor for merge_worktree.

    A commit_guard step is one whose name starts with 'commit_guard' OR whose
    tool is 'run_cmd' and whose cmd contains 'git commit'.
    """
    if step_name.startswith("commit_guard"):
        return True
    step = ctx.recipe.steps.get(step_name)
    if step and step.tool == "run_cmd":
        cmd = step.with_args.get("cmd", "")
        if "git commit" in cmd:
            return True
    return False


_RECOVERABLE_FAILED_STEPS: frozenset[str] = frozenset(
    {
        MergeFailedStep.DIRTY_TREE,
        MergeFailedStep.TEST_GATE,
        MergeFailedStep.POST_REBASE_TEST_GATE,
        MergeFailedStep.REBASE,
    }
)

_FAILED_STEP_PATTERN = re.compile(r"result\.failed_step\s*==\s*['\"](\w+)['\"]")


@semantic_rule(
    name="merge-routing-incomplete",
    description=(
        "Every merge_worktree step with predicate on_result must explicitly route "
        "all recoverable MergeFailedStep values to a recovery step. "
        "Unhandled values fall through to the result.error catch-all, which typically "
        "discards a recoverable worktree."
    ),
    severity=Severity.ERROR,
)
def _check_merge_routing_completeness(ctx: ValidationContext) -> list[RuleFinding]:
    findings: list[RuleFinding] = []
    for step_name, step in ctx.recipe.steps.items():
        if step.tool != "merge_worktree":
            continue
        if not step.on_result or not step.on_result.conditions:
            continue

        matched: set[str] = set()
        for condition in step.on_result.conditions:
            if condition.when is None:
                continue
            m = _FAILED_STEP_PATTERN.search(condition.when)
            if m:
                matched.add(m.group(1))

        missing = _RECOVERABLE_FAILED_STEPS - matched
        if missing:
            findings.append(
                RuleFinding(
                    rule="merge-routing-incomplete",
                    severity=Severity.ERROR,
                    step_name=step_name,
                    message=(
                        f"merge_worktree on_result is missing explicit routes for "
                        f"recoverable failures: {sorted(missing)}. "
                        f"These will fall through to the result.error catch-all, "
                        f"discarding a recoverable worktree."
                    ),
                )
            )
    return findings


def _has_commit_guard_ancestor(
    step_name: str, ctx: ValidationContext, *, max_depth: int = 5
) -> bool:
    """BFS over predecessors to find a commit_guard within *max_depth* hops."""
    visited: set[str] = set()
    frontier = ctx.predecessors.get(step_name, set())
    for _ in range(max_depth):
        if not frontier:
            break
        for p in frontier:
            if _is_commit_guard(p, ctx):
                return True
        visited |= frontier
        next_frontier: set[str] = set()
        for p in frontier:
            next_frontier |= ctx.predecessors.get(p, set()) - visited
        frontier = next_frontier
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
                RuleFinding(
                    rule="gh-pr-merge-silent-success-routing",
                    severity=Severity.ERROR,
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
    name="merge-without-commit-guard",
    description=(
        "A merge_worktree step has no commit_guard predecessor. Any path reaching "
        "merge with uncommitted changes will fail at the dirty-tree gate, burning "
        "an expensive recovery cycle. Add a commit_guard run_cmd step before merge."
    ),
    severity=Severity.ERROR,
)
def _check_merge_without_commit_guard(ctx: ValidationContext) -> list[RuleFinding]:
    findings: list[RuleFinding] = []
    for step_name, step in ctx.recipe.steps.items():
        if step.tool != "merge_worktree":
            continue
        if not _has_commit_guard_ancestor(step_name, ctx):
            findings.append(
                RuleFinding(
                    rule="merge-without-commit-guard",
                    severity=Severity.ERROR,
                    step_name=step_name,
                    message=(
                        f"merge_worktree step '{step_name}' has no commit_guard predecessor. "
                        f"Uncommitted changes from context-exhausted skills will trigger "
                        f"the dirty-tree gate, causing an expensive recovery cycle. "
                        f"Add a commit_guard run_cmd step immediately before this step."
                    ),
                )
            )
    return findings


# ---------------------------------------------------------------------------
# release-issue-on-unconfirmed-merge rule
# ---------------------------------------------------------------------------

_TIMEOUT_CONDITION_RE = re.compile(r"timeout", re.IGNORECASE)
_MERGE_WAIT_TOOLS = frozenset({"wait_for_merge_queue"})
_DIRECT_WAIT_NAMES = re.compile(r"wait_for_(direct|immediate)_merge")
_REGISTER_CLONE_UNCONFIRMED = "register_clone_unconfirmed"


def _collect_timeout_exit_steps(ctx: ValidationContext) -> set[str]:
    """Collect step names that are timeout exits from merge-wait steps.

    A timeout exit is any step name that appears as the route of an on_result
    condition containing 'timeout' in its when-expression, where the source
    step is a merge-wait step (wait_for_merge_queue, wait_for_direct_merge,
    wait_for_immediate_merge run_cmd steps). The on_failure of any merge-wait
    step is also treated as a timeout exit (tool error is also unconfirmed).
    """
    exits: set[str] = set()
    for step_name, step in ctx.recipe.steps.items():
        is_merge_wait = step.tool in _MERGE_WAIT_TOOLS or (
            step.tool == "run_cmd" and bool(_DIRECT_WAIT_NAMES.search(step_name))
        )
        if not is_merge_wait:
            continue
        if step.on_result and step.on_result.conditions:
            for cond in step.on_result.conditions:
                if cond.when and _TIMEOUT_CONDITION_RE.search(cond.when):
                    exits.add(cond.route)
        if step.on_failure:
            exits.add(step.on_failure)
    return exits


@semantic_rule(
    name="release-issue-on-unconfirmed-merge",
    description=(
        "A release_issue step must not be reachable from a merge-wait timeout exit. "
        "When wait_for_merge_queue / wait_for_direct_merge / wait_for_immediate_merge "
        "times out, the PR is still actively in the queue. Calling release_issue removes "
        "the in-progress label, leaving the issue visually unclaimed while the merge is "
        "still pending. Route timeout exits to register_clone_unconfirmed instead."
    ),
    severity=Severity.ERROR,
)
def _check_release_issue_on_unconfirmed_merge(ctx: ValidationContext) -> list[RuleFinding]:
    timeout_exits = _collect_timeout_exit_steps(ctx)
    if not timeout_exits:
        return []

    # BFS from all timeout exits using the forward step_graph
    reachable: set[str] = set()
    frontier = set(timeout_exits) & set(ctx.step_graph)
    while frontier:
        reachable |= frontier
        next_frontier: set[str] = set()
        for name in frontier:
            next_frontier |= ctx.step_graph.get(name, set()) - reachable
        frontier = next_frontier

    findings: list[RuleFinding] = []
    for step_name in reachable:
        step = ctx.recipe.steps.get(step_name)
        if step and step.tool == "release_issue":
            findings.append(
                RuleFinding(
                    rule="release-issue-on-unconfirmed-merge",
                    severity=Severity.ERROR,
                    step_name=step_name,
                    message=(
                        f"Step '{step_name}' calls release_issue but is reachable from a "
                        f"merge-wait timeout exit ({sorted(timeout_exits)}). "
                        f"Calling release_issue on a timeout path removes the in-progress label "
                        f"while the PR may still be queued. Replace with "
                        f"{_REGISTER_CLONE_UNCONFIRMED} (status: unconfirmed) so the label"
                        f" is kept."
                    ),
                )
            )
    return findings


# ---------------------------------------------------------------------------
# merge-enrollment-auto-consistency rule
# ---------------------------------------------------------------------------

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
        reachable = _bfs_reachable(ctx.step_graph, target) | {target}
        for reached in reachable:
            if _is_auto_flagged_step(reached, ctx):
                findings.append(
                    RuleFinding(
                        rule="merge-enrollment-auto-consistency",
                        severity=Severity.ERROR,
                        step_name=reached,
                        message=(
                            f"Step '{reached}' uses --auto or toggle_auto_merge but is "
                            f"reachable from an auto_merge_available=false routing arm "
                            f"(via '{target}'). Use enqueue_pr instead."
                        ),
                    )
                )
    return findings
