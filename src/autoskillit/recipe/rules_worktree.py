"""Worktree and retry validation rules for recipe pipelines."""

from __future__ import annotations

from autoskillit.core import (
    SKILL_TOOLS,
    Severity,
    get_logger,
)
from autoskillit.recipe.contracts import resolve_skill_name
from autoskillit.recipe.registry import RuleFinding, semantic_rule
from autoskillit.recipe.schema import Recipe

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
def _check_model_on_non_skill(wf: Recipe) -> list[RuleFinding]:
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
def _check_retries_on_worktree_creating_skill(wf: Recipe) -> list[RuleFinding]:
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
    name="retry-worktree-cwd",
    description="retry-worktree cwd must use a context variable so git runs inside the worktree.",
    severity=Severity.ERROR,
)
def _check_retry_worktree_cwd(wf: Recipe) -> list[RuleFinding]:
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
