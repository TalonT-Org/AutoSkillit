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
    description="The 'model' field only affects run_skill/run_skill_retry steps.",
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
                        f"run_skill and run_skill_retry. Remove it to avoid confusion."
                    ),
                )
            )
    return findings


@semantic_rule(
    name="retry-without-capture",
    description="run_skill_retry with retry must have capture if downstream uses context.",
    severity=Severity.WARNING,
)
def _check_retry_without_capture(wf: Recipe) -> list[RuleFinding]:
    findings: list[RuleFinding] = []
    step_names = list(wf.steps.keys())

    for idx, (step_name, step) in enumerate(wf.steps.items()):
        if step.tool == "run_skill_retry" and step.retry and not step.capture:
            downstream_needs_context = False
            for later_name in step_names[idx + 1 :]:
                later_step = wf.steps[later_name]
                for val in later_step.with_args.values():
                    if "context." in str(val):
                        downstream_needs_context = True
                        break
                if downstream_needs_context:
                    break

            if downstream_needs_context:
                findings.append(
                    RuleFinding(
                        rule="retry-without-capture",
                        severity=Severity.WARNING,
                        step_name=step_name,
                        message=(
                            f"Step '{step_name}' uses run_skill_retry with retry "
                            f"routing but has no capture block. A downstream step "
                            f"references context values — add a capture block to "
                            f"thread outputs (e.g., worktree_path, plan_path) forward."
                        ),
                    )
                )
    return findings


@semantic_rule(
    name="worktree-retry-creates-new",
    description="Worktree-creating skills must not have retry max_attempts > 1.",
    severity=Severity.ERROR,
)
def _check_worktree_retry_creates_new(
    wf: Recipe,
) -> list[RuleFinding]:
    findings: list[RuleFinding] = []
    for step_name, step in wf.steps.items():
        if step.tool not in SKILL_TOOLS:
            continue
        if not step.retry or step.retry.max_attempts <= 1:
            continue

        skill_cmd = step.with_args.get("skill_command", "")
        skill_name = resolve_skill_name(skill_cmd)
        if skill_name and skill_name in _WORKTREE_CREATING_SKILLS:
            findings.append(
                RuleFinding(
                    rule="worktree-retry-creates-new",
                    severity=Severity.ERROR,
                    step_name=step_name,
                    message=(
                        f"Step '{step_name}' retries {skill_name} "
                        f"with max_attempts="
                        f"{step.retry.max_attempts}. Each retry "
                        f"creates a new worktree, orphaning partial "
                        f"progress. Set max_attempts: 1 and route "
                        f"on_exhausted to a retry-worktree step that "
                        f"resumes in the existing worktree."
                    ),
                )
            )
    return findings


@semantic_rule(
    name="needs-retry-no-restart",
    description="Worktree-creating skills must not retry on needs_retry with max_attempts >= 1.",
    severity=Severity.ERROR,
)
def _check_needs_retry_no_restart(wf: Recipe) -> list[RuleFinding]:
    findings: list[RuleFinding] = []
    for step_name, step in wf.steps.items():
        if step.tool not in SKILL_TOOLS:
            continue
        if not step.retry:
            continue
        if step.retry.on != "needs_retry":
            continue
        if step.retry.max_attempts < 1:
            continue  # max_attempts: 0 is the correct pattern — escalates immediately
        skill_cmd = step.with_args.get("skill_command", "")
        skill_name = resolve_skill_name(skill_cmd)
        if skill_name and skill_name in _WORKTREE_CREATING_SKILLS:
            findings.append(
                RuleFinding(
                    rule="needs-retry-no-restart",
                    severity=Severity.ERROR,
                    step_name=step_name,
                    message=(
                        f"Step '{step_name}' retries worktree-creating skill "
                        f"'{skill_name}' on needs_retry "
                        f"(max_attempts={step.retry.max_attempts}). "
                        f"needs_retry signals partial progress exists — the skill "
                        f"must not restart. "
                        f"Set max_attempts: 0 to immediately escalate to on_exhausted."
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
