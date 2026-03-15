"""Semantic rules for skill_command resolvability."""

from __future__ import annotations

import functools
import re

from autoskillit.core import SKILL_TOOLS, Severity
from autoskillit.recipe._analysis import ValidationContext
from autoskillit.recipe.contracts import resolve_skill_name
from autoskillit.recipe.registry import RuleFinding, semantic_rule
from autoskillit.workspace import SkillResolver


@functools.lru_cache(maxsize=1)
def _get_bundled_skill_names() -> frozenset[str]:
    return frozenset(s.name for s in SkillResolver().list_all())


_SKILL_TOKEN_RE = re.compile(r"/autoskillit:(\S+)")


def _has_dynamic_skill_name(skill_cmd: str) -> bool:
    """Return True if the skill name portion contains template expressions."""
    m = _SKILL_TOKEN_RE.search(skill_cmd)
    if not m:
        return False
    token = m.group(1)
    first_space = token.find(" ")
    name_part = token[:first_space] if first_space >= 0 else token
    return "${{" in name_part


@semantic_rule(
    name="unknown-skill-command",
    description="run_skill step skill_command must reference a known bundled skill",
    severity=Severity.ERROR,
)
def _check_unknown_skill_command(ctx: ValidationContext) -> list[RuleFinding]:
    findings: list[RuleFinding] = []
    known = ctx.available_skills or _get_bundled_skill_names()
    for step_name, step in ctx.recipe.steps.items():
        if step.tool not in SKILL_TOOLS:
            continue
        skill_cmd = step.with_args.get("skill_command", "")
        if _has_dynamic_skill_name(skill_cmd):
            continue
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
