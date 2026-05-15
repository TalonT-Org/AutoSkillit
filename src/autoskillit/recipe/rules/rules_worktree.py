"""Worktree and retry validation rules for recipe pipelines."""

from __future__ import annotations

from autoskillit.core import (
    SKILL_TOOLS,
    Severity,
    get_logger,
)
from autoskillit.recipe._analysis import ValidationContext
from autoskillit.recipe.contracts import INPUT_REF_RE, load_bundled_manifest, resolve_skill_name
from autoskillit.recipe.io import iter_steps_with_context
from autoskillit.recipe.registry import RuleFinding, semantic_rule

logger = get_logger(__name__)

_WORKTREE_MODIFYING_SKILLS = frozenset(
    {
        "implement-worktree",
        "implement-worktree-no-merge",
        "implement-experiment",
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
    name="retries-on-worktree-modifying-skill",
    description="Worktree-modifying skills must not have retries > 0.",
    severity=Severity.ERROR,
)
def _check_retries_on_worktree_modifying_skill(ctx: ValidationContext) -> list[RuleFinding]:
    wf = ctx.recipe
    findings: list[RuleFinding] = []
    for step_name, step in wf.steps.items():
        if step.tool not in SKILL_TOOLS:
            continue
        if step.retries <= 0:
            continue
        skill_cmd = step.with_args.get("skill_command", "")
        skill_name = resolve_skill_name(skill_cmd)
        if skill_name and skill_name in _WORKTREE_MODIFYING_SKILLS:
            findings.append(
                RuleFinding(
                    rule="retries-on-worktree-modifying-skill",
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
        "A step invoking a worktree-modifying skill with retries:0 has no on_context_limit "
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
        if not skill or skill not in _WORKTREE_MODIFYING_SKILLS:
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


@semantic_rule(
    name="relative-worktree-path-in-cmd",
    description=(
        "run_cmd steps must not use relative '../worktrees/' paths. "
        "Resolve the main worktree root via git rev-parse --git-common-dir "
        "and compute an absolute worktree path from there."
    ),
    severity=Severity.WARNING,
)
def _check_relative_worktree_path_in_cmd(ctx: ValidationContext) -> list[RuleFinding]:
    wf = ctx.recipe
    findings: list[RuleFinding] = []
    for step_name, step in wf.steps.items():
        if step.tool != "run_cmd":
            continue
        cmd = step.with_args.get("cmd", "")
        if "../worktrees/" in cmd:
            findings.append(
                RuleFinding(
                    rule="relative-worktree-path-in-cmd",
                    severity=Severity.WARNING,
                    step_name=step_name,
                    message=(
                        f"Step '{step_name}' uses a relative '../worktrees/' path in its cmd. "
                        f"This causes nested worktree directories when source_dir is "
                        f"itself a worktree. Resolve the main repo root via "
                        f"'git rev-parse --path-format=absolute --git-common-dir' first."
                    ),
                )
            )
    return findings


@semantic_rule(
    name="file-writing-skill-missing-context-limit",
    description=(
        "A step invoking a write_behavior='always' skill has no on_context_limit route. "
        "If the session hits a context limit mid-edit, uncommitted changes strand on disk "
        "and the step falls through to on_failure, losing progress."
    ),
    severity=Severity.WARNING,
)
def _check_file_writing_skill_missing_context_limit(
    ctx: ValidationContext,
) -> list[RuleFinding]:
    wf = ctx.recipe
    findings: list[RuleFinding] = []

    manifest = load_bundled_manifest()
    if manifest is None:
        logger.warning(
            "file-writing-skill-missing-context-limit: failed to load manifest; skipping"
        )
        return findings

    skills = manifest.get("skills", {})

    for step_name, step in wf.steps.items():
        if step.tool not in SKILL_TOOLS:
            continue
        # Advisory steps (skip_when_false) are covered by advisory-step-missing-context-limit.
        if step.skip_when_false:
            continue
        if step.on_context_limit is not None:
            continue
        skill_cmd = step.with_args.get("skill_command", "")
        skill = resolve_skill_name(skill_cmd)
        if not skill:
            continue
        skill_data = skills.get(skill, {})
        if skill_data.get("write_behavior") != "always":
            continue
        findings.append(
            RuleFinding(
                rule="file-writing-skill-missing-context-limit",
                severity=Severity.WARNING,
                step_name=step_name,
                message=(
                    f"Step '{step_name}' invokes '{skill}' (write_behavior=always) "
                    f"but has no on_context_limit route. Context exhaustion mid-edit "
                    f"will strand uncommitted changes on disk and fall through to "
                    f"on_failure, losing progress. Add on_context_limit routing."
                ),
            )
        )
    return findings


@semantic_rule(
    name="superseded-input-after-capture",
    description=(
        "A step uses inputs.X as cwd or in skill_command after a "
        "worktree-modifying skill captured context.X. The context variable "
        "holds the current worktree path; using the input references the "
        "original (pre-capture) worktree."
    ),
    severity=Severity.ERROR,
)
def _check_superseded_input_after_capture(ctx: ValidationContext) -> list[RuleFinding]:
    wf = ctx.recipe
    findings: list[RuleFinding] = []
    superseded_keys: set[str] = set()

    for step_name, step, _available in iter_steps_with_context(wf):
        if superseded_keys:
            for field_name in ("cwd", "skill_command"):
                value = step.with_args.get(field_name, "")
                for key in INPUT_REF_RE.findall(value):
                    if key in superseded_keys:
                        findings.append(
                            RuleFinding(
                                rule="superseded-input-after-capture",
                                severity=Severity.ERROR,
                                step_name=step_name,
                                message=(
                                    f"Step '{step_name}' references inputs.{key} in "
                                    f"{field_name} after a worktree-modifying skill "
                                    f"captured context.{key}. Use context.{key} instead."
                                ),
                            )
                        )

        if step.tool in SKILL_TOOLS and step.capture:
            skill_cmd = step.with_args.get("skill_command", "")
            skill = resolve_skill_name(skill_cmd)
            if skill and skill in _WORKTREE_MODIFYING_SKILLS:
                superseded_keys.update(step.capture.keys())

    return findings


@semantic_rule(
    name="capture-list-requires-retries-zero",
    description=(
        "capture_list steps must set retries: 0. "
        "Each retry re-initializes the accumulated list, producing duplicates."
    ),
    severity=Severity.ERROR,
)
def _check_capture_list_requires_retries_zero(ctx: ValidationContext) -> list[RuleFinding]:
    wf = ctx.recipe
    findings: list[RuleFinding] = []
    for step_name, step in wf.steps.items():
        if step.capture_list and step.retries > 0:
            findings.append(
                RuleFinding(
                    rule="capture-list-requires-retries-zero",
                    severity=Severity.ERROR,
                    step_name=step_name,
                    message=(
                        f"Step '{step_name}' uses capture_list (accumulated across "
                        f"lens iterations) but has retries: {step.retries}. Each retry "
                        f"re-initializes the list, producing duplicate entries. "
                        f"Set retries: 0 and use on_context_limit routing to resume "
                        f"in the existing worktree."
                    ),
                )
            )
    return findings
