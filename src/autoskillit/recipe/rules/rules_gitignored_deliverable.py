"""Lint rule: plan steps writing to gitignored directories that feed audit-impl.

Plan steps whose ``output_dir`` resolves under ``{{AUTOSKILLIT_TEMP}}`` (a
gitignored directory) and that have a downstream ``audit-impl`` step in their
reachability graph will produce unresolvable MISSING findings — the plan
deliverable is invisible to ``git diff`` and therefore to audit-impl.

This rule fires a WARNING so recipe authors are alerted to the pattern.
"""

from __future__ import annotations

from autoskillit.core import Severity
from autoskillit.recipe._analysis import ValidationContext
from autoskillit.recipe._analysis_bfs import bfs_reachable
from autoskillit.recipe.contracts import resolve_skill_name
from autoskillit.recipe.registry import RuleFinding, make_finding, semantic_rule

_AUTOSKILLIT_TEMP_PREFIX = "{{AUTOSKILLIT_TEMP}}"


def _step_writes_to_gitignored(step) -> bool:
    output_dir = (step.with_args or {}).get("output_dir", "")
    if not isinstance(output_dir, str):
        return False
    return _AUTOSKILLIT_TEMP_PREFIX in output_dir


def _downstream_audit_impl_exists(ctx: ValidationContext, start: str) -> bool:
    reachable = bfs_reachable(ctx.step_graph, start)
    for step_name in reachable:
        step = ctx.recipe.steps.get(step_name)
        if step is None or step.tool != "run_skill":
            continue
        skill = resolve_skill_name(step.with_args.get("skill_command", ""))
        if skill == "audit-impl":
            return True
    return False


@semantic_rule(
    name="gitignored-deliverable-in-plan",
    description=(
        "Plan steps writing to gitignored directories (e.g. {{AUTOSKILLIT_TEMP}}/) "
        "that feed into audit-impl produce unresolvable MISSING findings."
    ),
    severity=Severity.WARNING,
)
def _check_gitignored_deliverable_in_plan(ctx: ValidationContext) -> list[RuleFinding]:  # noqa: F401
    findings = []
    for step_name, step in ctx.recipe.steps.items():
        if not _step_writes_to_gitignored(step):
            continue
        if not _downstream_audit_impl_exists(ctx, step_name):
            continue
        findings.append(
            make_finding(
                rule_name="gitignored-deliverable-in-plan",
                step_name=step_name,
                message=(
                    f"Step '{step_name}' writes to a gitignored path "
                    f"({_AUTOSKILLIT_TEMP_PREFIX}/) and has a downstream "
                    "audit-impl step. Gitignored plan deliverables cannot "
                    "appear in git diff and will cause audit-impl to issue "
                    "unresolvable MISSING findings. Consider using a tracked "
                    "output location or restructuring the pipeline so the "
                    "plan step does not feed directly into audit-impl."
                ),
            )
        )
    return findings
