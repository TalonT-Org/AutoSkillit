"""Semantic rules for skill_command resolvability."""

from __future__ import annotations

import functools
import re

from autoskillit.core import AUTOSKILLIT_SKILL_PREFIX, SKILL_TOOLS, Severity
from autoskillit.recipe._analysis import ValidationContext
from autoskillit.recipe.contracts import resolve_skill_name
from autoskillit.recipe.registry import RuleFinding, semantic_rule
from autoskillit.workspace import SkillResolver, detect_project_local_overrides


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


@semantic_rule(
    name="project-local-skill-override",
    description=(
        "run_skill step references /autoskillit:<name> but a project-local override exists "
        "for that skill name — use bare /<name> instead so the project-local version is loaded"
    ),
    severity=Severity.WARNING,
)
def _check_project_local_skill_override(ctx: ValidationContext) -> list[RuleFinding]:
    if ctx.project_dir is None:
        return []
    overrides = detect_project_local_overrides(ctx.project_dir)
    if not overrides:
        return []
    findings: list[RuleFinding] = []
    for step_name, step in ctx.recipe.steps.items():
        if step.tool not in SKILL_TOOLS:
            continue
        skill_cmd = step.with_args.get("skill_command", "")
        if not skill_cmd.startswith(AUTOSKILLIT_SKILL_PREFIX):
            continue
        # Extract the skill name portion (strip prefix and any trailing args)
        name_part = skill_cmd[len(AUTOSKILLIT_SKILL_PREFIX) :]
        space = name_part.find(" ")
        skill_name = name_part[:space] if space >= 0 else name_part
        if skill_name in overrides:
            findings.append(
                RuleFinding(
                    rule="project-local-skill-override",
                    severity=Severity.WARNING,
                    step_name=step_name,
                    message=(
                        f"step '{step_name}': skill_command '{skill_cmd}' references "
                        f"bundled skill '{skill_name}' but a project-local override exists. "
                        f"Use '/{skill_name}' (bare command) so the project-local version "
                        f"is discovered by the headless session."
                    ),
                )
            )
    return findings
