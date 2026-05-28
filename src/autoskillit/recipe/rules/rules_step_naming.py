"""Semantic rules for step-key naming collision detection."""

from __future__ import annotations

from autoskillit.core import SKILL_TOOLS, Severity
from autoskillit.recipe._analysis import ValidationContext
from autoskillit.recipe._skill_helpers import _get_bundled_skill_names
from autoskillit.recipe.contracts import resolve_skill_name
from autoskillit.recipe.registry import RuleFinding, semantic_rule


@semantic_rule(
    name="step-skill-name-mismatch",
    description=(
        "run_skill step key matches a known skill different from the invoked skill_command"
    ),
    severity=Severity.WARNING,
)
def _check_step_skill_name_mismatch(ctx: ValidationContext) -> list[RuleFinding]:
    findings: list[RuleFinding] = []
    known = ctx.available_skills or _get_bundled_skill_names()
    for step_name, step in ctx.recipe.steps.items():
        if step.tool not in SKILL_TOOLS:
            continue
        skill_cmd = step.with_args.get("skill_command", "")
        invoked_skill = resolve_skill_name(skill_cmd)
        if invoked_skill is None:
            continue
        normalized_key = step_name.replace("_", "-")
        if normalized_key in known and normalized_key != invoked_skill:
            findings.append(
                RuleFinding(
                    rule="step-skill-name-mismatch",
                    severity=Severity.WARNING,
                    step_name=step_name,
                    message=(
                        f"step '{step_name}' invokes skill '{invoked_skill}' but "
                        f"the step key matches a different known skill "
                        f"'{normalized_key}'. Rename the step to avoid "
                        f"orchestrator confusion."
                    ),
                )
            )
    return findings
