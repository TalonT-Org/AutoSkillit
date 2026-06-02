"""Semantic rule: backend-incompatible skill detection."""

from __future__ import annotations

from autoskillit.core import SKILL_TOOLS, Severity
from autoskillit.recipe._analysis import ValidationContext
from autoskillit.recipe._skill_helpers import _has_dynamic_skill_name
from autoskillit.recipe.contracts import resolve_skill_name
from autoskillit.recipe.registry import RuleFinding, semantic_rule


@semantic_rule(
    name="backend-incompatible-skill",
    description=(
        "run_skill step references a skill whose backend_requirements "
        "exclude the recipe's target backend"
    ),
    severity=Severity.WARNING,
)
def _check_backend_incompatible_skill(ctx: ValidationContext) -> list[RuleFinding]:
    if ctx.backend_name is None:
        return []
    if ctx.skill_resolver is None:
        return []
    findings: list[RuleFinding] = []
    for step_name, step in ctx.recipe.steps.items():
        if step.tool not in SKILL_TOOLS:
            continue
        skill_cmd = step.with_args.get("skill_command", "")
        if _has_dynamic_skill_name(skill_cmd):
            continue
        skill_name = resolve_skill_name(skill_cmd)
        if skill_name is None:
            continue
        skill_info = ctx.skill_resolver.resolve(skill_name)
        if skill_info is None:
            continue
        reqs: frozenset[str] = getattr(skill_info, "backend_requirements", frozenset())
        if reqs and ctx.backend_name not in reqs:
            findings.append(
                RuleFinding(
                    rule="backend-incompatible-skill",
                    severity=Severity.WARNING,
                    step_name=step_name,
                    message=(
                        f"step '{step_name}': skill '{skill_name}' requires backend "
                        f"{sorted(reqs)} but recipe targets "
                        f"backend '{ctx.backend_name}'."
                    ),
                )
            )
    return findings
