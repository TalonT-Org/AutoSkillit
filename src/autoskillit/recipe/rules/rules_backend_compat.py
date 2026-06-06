"""Semantic rule: backend-incompatible skill detection."""

from __future__ import annotations

from autoskillit.core import SKILL_TOOLS, Severity
from autoskillit.core.types._type_backend import CLAUDE_CODE_CAPABILITIES
from autoskillit.core.types._type_constants_registries import SKILL_CAPABILITY_REGISTRY
from autoskillit.recipe._analysis import ValidationContext
from autoskillit.recipe._skill_helpers import _has_dynamic_skill_name
from autoskillit.recipe.contracts import resolve_skill_name
from autoskillit.recipe.registry import RuleFinding, semantic_rule

_GIT_METADATA_WRITE_CAP = "git_metadata_write"

_GIT_METADATA_WRITABLE_DEFAULT = CLAUDE_CODE_CAPABILITIES.git_metadata_writable


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
        if (
            skill_info.backend_requirements
            and ctx.backend_name not in skill_info.backend_requirements
        ):
            cap_def = SKILL_CAPABILITY_REGISTRY.get(_GIT_METADATA_WRITE_CAP)
            git_detail = (
                f" (backend lacks git_metadata_writable={_GIT_METADATA_WRITABLE_DEFAULT!r})"
                if cap_def and _GIT_METADATA_WRITE_CAP in skill_info.uses_capabilities
                else ""
            )
            findings.append(
                RuleFinding(
                    rule="backend-incompatible-skill",
                    severity=Severity.WARNING,
                    step_name=step_name,
                    message=(
                        f"step '{step_name}': skill '{skill_name}' requires backend "
                        f"{sorted(skill_info.backend_requirements)} but recipe targets "
                        f"backend '{ctx.backend_name}'.{git_detail}"
                    ),
                )
            )
    return findings
