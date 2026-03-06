"""Semantic rules for merge_worktree routing completeness."""

from __future__ import annotations

import re

from autoskillit.core import MergeFailedStep, Severity, get_logger
from autoskillit.recipe._analysis import ValidationContext
from autoskillit.recipe.registry import RuleFinding, semantic_rule

logger = get_logger(__name__)

# The subset of MergeFailedStep values where the worktree remains usable and
# resolve-failures can be applied. Non-recoverable values (PATH_VALIDATION,
# BRANCH_DETECTION, FETCH, PRE_REBASE_CHECK, MERGE) involve infrastructure or
# main-repo states where recipe-level recovery routing is not applicable.
_RECOVERABLE_FAILED_STEPS: frozenset[str] = frozenset({
    MergeFailedStep.TEST_GATE,              # pre-rebase test failure — worktree intact
    MergeFailedStep.POST_REBASE_TEST_GATE,  # post-rebase test failure — worktree intact
    MergeFailedStep.REBASE,                 # rebase conflict, abort succeeded — worktree intact
})

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
