"""Semantic rules for merge_worktree commit_guard enforcement (R4, R6)."""

from __future__ import annotations

import regex as re

from autoskillit.core import Severity
from autoskillit.recipe._analysis import ValidationContext, bfs_reachable
from autoskillit.recipe._rule_helpers import _is_loop_guard_step
from autoskillit.recipe.registry import RuleFinding, make_finding, semantic_rule


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
    name="merge-fix-cycle-without-iteration-guard",
    description=(
        "A merge_worktree step routes recoverable failures to a fix/assess step, "
        "creating a merge→fix→test→merge cycle. Without a check_loop_iteration guard, "
        "this cycle can loop unboundedly on structurally unresolvable conflicts."
    ),
    severity=Severity.ERROR,
)
def _check_merge_fix_cycle_without_guard(ctx: ValidationContext) -> list[RuleFinding]:
    findings: list[RuleFinding] = []
    for step_name, step in ctx.recipe.steps.items():
        if step.tool != "merge_worktree":
            continue
        if not step.on_result or not step.on_result.conditions:
            continue

        fix_routes: set[str] = set()
        for cond in step.on_result.conditions:
            if cond.when and re.search(r"\bfailed_step\b", cond.when):
                fix_routes.add(cond.route)

        if not fix_routes:
            continue

        has_guard = False
        for fix_step_name in fix_routes:
            reachable = bfs_reachable(ctx.step_graph, fix_step_name) | {fix_step_name}
            for reached in reachable:
                if _is_loop_guard_step(reached, ctx):
                    has_guard = True
                    break
            if has_guard:
                break

        if not has_guard:
            findings.append(
                make_finding(
                    rule_name="merge-fix-cycle-without-iteration-guard",
                    step_name=step_name,
                    message=(
                        f"merge_worktree step '{step_name}' routes recoverable failures "
                        f"to {sorted(fix_routes)}, creating a merge→fix→test cycle "
                        f"with no check_loop_iteration guard. This can loop unboundedly "
                        f"on structurally unresolvable conflicts. Add a check_merge_fix_loop "
                        f"step (run_python calling check_loop_iteration) between test and "
                        f"commit_guard to cap the cycle at 3 iterations."
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
                make_finding(
                    rule_name="merge-without-commit-guard",
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
