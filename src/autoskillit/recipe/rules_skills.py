"""Semantic rules for skill_command resolvability."""

from __future__ import annotations

from autoskillit.core import SKILL_TOOLS, Severity
from autoskillit.recipe._analysis import ValidationContext
from autoskillit.recipe.contracts import resolve_skill_name
from autoskillit.recipe.registry import RuleFinding, semantic_rule
from autoskillit.workspace.skills import SkillResolver

_BUNDLED_SKILL_NAMES: frozenset[str] = frozenset(s.name for s in SkillResolver().list_all())


@semantic_rule(
    name="unknown-skill-command",
    description="run_skill step skill_command must reference a known bundled skill",
    severity=Severity.ERROR,
)
def _check_unknown_skill_command(ctx: ValidationContext) -> list[RuleFinding]:
    findings: list[RuleFinding] = []
    known = ctx.available_skills or _BUNDLED_SKILL_NAMES
    for step_name, step in ctx.recipe.steps.items():
        if step.tool not in SKILL_TOOLS:
            continue
        skill_cmd = step.with_args.get("skill_command", "")
        skill_name = resolve_skill_name(skill_cmd)
        if skill_name is None:
            continue
        if skill_name not in known:
            findings.append(
                RuleFinding(
                    rule="unknown-skill-command",
                    severity=Severity.ERROR,
                    step_name=step_name,
                    message=(
                        f"step '{step_name}': skill_command '{skill_cmd}' references "
                        f"unknown skill '{skill_name}'. "
                        f"Known bundled skills: {sorted(known)}"
                    ),
                )
            )
    return findings
