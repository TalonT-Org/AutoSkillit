"""Semantic rule: commit_guard steps with base_branch must route regression_detected.

The commit_guard callable returns {"committed": "regression_detected"} when it
detects that pending changes would revert implementation work. Without an
on_result clause matching that value, the regression is silently swallowed
and the pipeline continues with a dirty worktree, eventually failing with a
misleading error.
"""

from __future__ import annotations

from autoskillit.core import Severity
from autoskillit.recipe._analysis import ValidationContext
from autoskillit.recipe.registry import RuleFinding, make_finding, semantic_rule
from autoskillit.recipe.schema import RecipeStep

_COMMIT_GUARD_CALLABLE = "autoskillit.recipe._cmd_rpc.commit_guard"
_REGRESSION_PREDICATE = "regression_detected"


def _has_regression_route(step: RecipeStep) -> bool:
    if step.on_result is None:
        return False
    for cond in step.on_result.conditions:
        if cond.when is not None and _REGRESSION_PREDICATE in cond.when:
            return True
    return False


@semantic_rule(
    name="commit-guard-regression-route-missing",
    description=(
        "commit_guard steps with a non-empty base_branch must declare an on_result "
        "predicate that routes the regression_detected result. Without it the "
        "regression is silently treated as a success."
    ),
    severity=Severity.ERROR,
)
def _check_commit_guard_regression_route(ctx: ValidationContext) -> list[RuleFinding]:
    wf = ctx.recipe
    findings: list[RuleFinding] = []
    for step_name, step in wf.steps.items():
        if step.tool != "run_python":
            continue
        if step.with_args.get("callable") != _COMMIT_GUARD_CALLABLE:
            continue
        base_branch = step.with_args.get("base_branch", "")
        if not base_branch or base_branch.strip() == "":
            continue
        if _has_regression_route(step):
            continue
        findings.append(
            make_finding(
                rule_name="commit-guard-regression-route-missing",
                step_name=step_name,
                message=(
                    f"Step '{step_name}' calls commit_guard with base_branch but "
                    f"has no on_result predicate routing regression_detected. "
                    f"Add an on_result block: "
                    f"- when: ${{{{ result.committed }}}} == regression_detected "
                    f"  route: <escalation_step>"
                ),
            )
        )
    return findings
