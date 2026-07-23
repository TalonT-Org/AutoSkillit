"""Semantic rule: backend-incompatible skill detection."""

from __future__ import annotations

from autoskillit.core import (
    CLAUDE_CODE_CAPABILITIES,
    SKILL_CAPABILITY_REGISTRY,
    SKILL_TOOLS,
    Severity,
    SkillContractError,
    SkillExecutionRole,
    describe_capability_mismatches,
    unsatisfied_backend_capabilities,
)
from autoskillit.recipe._analysis import ValidationContext
from autoskillit.recipe._skill_helpers import _has_dynamic_skill_name
from autoskillit.recipe.contracts import resolve_skill_name
from autoskillit.recipe.registry import RuleFinding, make_finding, semantic_rule

_GIT_METADATA_WRITE_CAP = "git_metadata_write"


@semantic_rule(
    name="backend-incompatible-skill",
    description=(
        "run_skill step references a skill whose backend_requirements "
        "exclude the recipe's target backend"
    ),
    severity=Severity.ERROR,
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
        try:
            invocation = ctx.skill_resolver.resolve_invocation(
                skill_name,
                ctx.project_dir,
                SkillExecutionRole.SESSION,
            )
        except SkillContractError as exc:
            findings.append(
                make_finding(
                    rule_name="backend-incompatible-skill",
                    step_name=step_name,
                    message=(
                        f"step '{step_name}': skill '{skill_name}' has no valid effective "
                        f"invocation: {exc}"
                    ),
                )
            )
            continue
        # Per-step effective backend: when providers route a single step to
        # a different backend (ANTHROPIC_BASE_URL → claude-code), the global
        # ctx.backend_name would incorrectly flag that covered step.
        step_backend = (
            ctx.effective_backend_map.get(step_name, ctx.backend_name)
            if ctx.effective_backend_map
            else ctx.backend_name
        )
        if step_backend is None:
            continue
        uses_caps = invocation.capability_union
        _step_origin = (ctx.backend_origin_map or {}).get(step_name)
        _step_remedy = (
            (
                f"Remove or change '{_step_origin}' in ~/.autoskillit/config.yaml "
                "or <project>/.autoskillit/config.yaml, or pin a backend with the "
                "required capability."
            )
            if _step_origin
            else None
        )
        if invocation.backend_requirements and step_backend not in invocation.backend_requirements:
            cap_def = SKILL_CAPABILITY_REGISTRY.get(_GIT_METADATA_WRITE_CAP)
            _required = CLAUDE_CODE_CAPABILITIES.git_metadata_writable
            git_detail = (
                f" (requires git_metadata_writable={_required!r})"
                if cap_def and _GIT_METADATA_WRITE_CAP in uses_caps
                else ""
            )
            findings.append(
                make_finding(
                    rule_name="backend-incompatible-skill",
                    step_name=step_name,
                    message=(
                        f"step '{step_name}': skill '{skill_name}' requires backend "
                        f"{sorted(invocation.backend_requirements)} but recipe targets "
                        f"backend '{step_backend}'.{git_detail}"
                    ),
                    origin=_step_origin,
                    remedy=_step_remedy,
                )
            )
        # Independent hard-capability property check (sibling if, not elif) —
        # catches worker_routable skills whose backend_requirements are empty
        # but require a backend property (e.g. git_metadata_write → requires
        # git_metadata_writable=True on the pinned backend). Different actionable
        # diagnostic from backend_requirements — both findings surface together.
        step_backend_caps = (ctx.backend_capabilities_map or {}).get(step_backend)
        if step_backend_caps and uses_caps:
            mismatches = unsatisfied_backend_capabilities(uses_caps, step_backend_caps)
            if mismatches:
                findings.append(
                    make_finding(
                        rule_name="backend-incompatible-skill",
                        step_name=step_name,
                        message=(
                            f"step '{step_name}': skill '{skill_name}' requires backend "
                            f"'{step_backend}' to satisfy: "
                            f"{describe_capability_mismatches(mismatches)}."
                        ),
                        origin=_step_origin,
                        remedy=_step_remedy,
                    )
                )
    return findings
