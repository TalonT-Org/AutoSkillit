"""Semantic rules for merge_worktree routing completeness."""

from __future__ import annotations

import re

from autoskillit.core import MergeFailedStep, Severity, get_logger
from autoskillit.recipe._analysis import ValidationContext
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
        preds = ctx.predecessors.get(step_name, set())
        if not any(_is_commit_guard(p, ctx) for p in preds):
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
