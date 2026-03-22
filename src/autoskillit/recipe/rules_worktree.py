"""Worktree and retry validation rules for recipe pipelines."""

from __future__ import annotations

from autoskillit.core import (
    SKILL_TOOLS,
    Severity,
    get_logger,
)
from autoskillit.recipe._analysis import ValidationContext
from autoskillit.recipe.contracts import resolve_skill_name
from autoskillit.recipe.registry import RuleFinding, semantic_rule

logger = get_logger(__name__)

_WORKTREE_CREATING_SKILLS = frozenset(
    {
        "implement-worktree",
        "implement-worktree-no-merge",
    }
)


@semantic_rule(
    name="model-on-non-skill-step",
    description="The 'model' field only affects run_skill steps.",
    severity=Severity.WARNING,
)
def _check_model_on_non_skill(ctx: ValidationContext) -> list[RuleFinding]:
    wf = ctx.recipe
    findings: list[RuleFinding] = []
    for step_name, step in wf.steps.items():
        if step.model and step.tool not in SKILL_TOOLS:
            findings.append(
                RuleFinding(
                    rule="model-on-non-skill-step",
                    severity=Severity.WARNING,
                    step_name=step_name,
                    message=(
                        f"Step '{step_name}' has 'model: {step.model}' but uses "
                        f"tool '{step.tool}'. The model field only affects "
                        f"run_skill. Remove it to avoid confusion."
                    ),
                )
            )
    return findings


@semantic_rule(
    name="retries-on-worktree-creating-skill",
    description="Worktree-creating skills must not have retries > 0.",
    severity=Severity.ERROR,
)
def _check_retries_on_worktree_creating_skill(ctx: ValidationContext) -> list[RuleFinding]:
    wf = ctx.recipe
    findings: list[RuleFinding] = []
    for step_name, step in wf.steps.items():
        if step.tool not in SKILL_TOOLS:
            continue
        if step.retries <= 0:
            continue
        skill_cmd = step.with_args.get("skill_command", "")
        skill_name = resolve_skill_name(skill_cmd)
        if skill_name and skill_name in _WORKTREE_CREATING_SKILLS:
            findings.append(
                RuleFinding(
                    rule="retries-on-worktree-creating-skill",
                    severity=Severity.ERROR,
                    step_name=step_name,
                    message=(
                        f"Step '{step_name}' creates a worktree but has "
                        f"`retries: {step.retries}`. Each retry creates a new orphaned "
                        f"worktree. Set `retries: 0` and use "
                        f"`on_context_limit: <resume-step>` to resume in the existing worktree."
                    ),
                )
            )
    return findings


@semantic_rule(
    name="missing-context-limit-on-worktree",
    description=(
        "A step invoking a worktree-creating skill with retries:0 has no on_context_limit "
        "route. If the session hits a context limit, the worktree partial progress is "
        "unreachable: the step falls through to on_failure instead of routing to retry_worktree. "
        "Add on_context_limit pointing to a retry_worktree step to preserve partial progress."
    ),
    severity=Severity.WARNING,
)
def _check_missing_context_limit_on_worktree(ctx: ValidationContext) -> list[RuleFinding]:
    wf = ctx.recipe
    findings: list[RuleFinding] = []
    for step_name, step in wf.steps.items():
        if step.tool not in SKILL_TOOLS:
            continue
        skill_cmd = step.with_args.get("skill_command", "")
        skill = resolve_skill_name(skill_cmd)
        if not skill or skill not in _WORKTREE_CREATING_SKILLS:
            continue
        if step.retries <= 0 and step.on_context_limit is None:
            findings.append(
                RuleFinding(
                    rule="missing-context-limit-on-worktree",
                    severity=Severity.WARNING,
                    step_name=step_name,
                    message=(
                        f"Step '{step_name}' invokes '{skill}' with retries:0 "
                        f"but has no on_context_limit route. Partial worktree progress "
                        f"is unreachable if the session hits a context limit."
                    ),
                )
            )
    return findings


@semantic_rule(
    name="advisory-step-missing-context-limit",
    description=(
        "A run_skill step with skip_when_false declared (advisory/optional) must define "
        "on_context_limit. The toggle skip_when_false says the step may be skipped by config; "
        "the absence of on_context_limit means it cannot be skipped on context exhaustion — "
        "an inconsistency in the skippability contract."
    ),
    severity=Severity.WARNING,
)
def _advisory_step_missing_context_limit(ctx: ValidationContext) -> list[RuleFinding]:
    findings: list[RuleFinding] = []
    for step_name, step in ctx.recipe.steps.items():
        if step.tool not in SKILL_TOOLS:
            continue
        if step.skip_when_false is None:
            continue
        if step.on_context_limit is not None:
            continue
        findings.append(
            RuleFinding(
                rule="advisory-step-missing-context-limit",
                severity=Severity.WARNING,
                step_name=step_name,
                message=(
                    f"Step '{step_name}' is advisory (skip_when_false={step.skip_when_false!r}) "
                    f"but declares no on_context_limit. A step that can be skipped by "
                    f"configuration must also handle context exhaustion gracefully. "
                    f"Set on_context_limit to the same target as on_success to skip the "
                    f"advisory step on context limit."
                ),
            )
        )
    return findings


@semantic_rule(
    name="retry-worktree-cwd",
    description="retry-worktree cwd must use a context variable so git runs inside the worktree.",
    severity=Severity.ERROR,
)
def _check_retry_worktree_cwd(ctx: ValidationContext) -> list[RuleFinding]:
    wf = ctx.recipe
    findings: list[RuleFinding] = []
    for step_name, step in wf.steps.items():
        if step.tool not in SKILL_TOOLS:
            continue
        skill_cmd = step.with_args.get("skill_command", "")
        if resolve_skill_name(skill_cmd) != "retry-worktree":
            continue
        cwd = step.with_args.get("cwd", "")
        if "${{ context." not in cwd:
            findings.append(
                RuleFinding(
                    rule="retry-worktree-cwd",
                    severity=Severity.ERROR,
                    step_name=step_name,
                    message=f"Step '{step_name}': retry-worktree cwd must use a context variable.",
                )
            )
    return findings
