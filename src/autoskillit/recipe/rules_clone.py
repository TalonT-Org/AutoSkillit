"""Semantic validation rules for clone, push, and multipart-plan capture."""

from __future__ import annotations

from autoskillit.core import (
    SKILL_COMMAND_PREFIX,
    SKILL_TOOLS,
    Severity,
    get_logger,
)
from autoskillit.recipe._analysis import ValidationContext
from autoskillit.recipe.registry import RuleFinding, semantic_rule

logger = get_logger(__name__)


@semantic_rule(
    name="multipart-plan-parts-not-captured",
    description="Multi-part plan recipes must capture plan_parts via capture_list.",
    severity=Severity.ERROR,
)
def _check_plan_parts_captured(ctx: ValidationContext) -> list[RuleFinding]:
    wf = ctx.recipe
    _MULTIPART_SKILLS = {"/autoskillit:make-plan", "/autoskillit:rectify"}
    findings: list[RuleFinding] = []

    for step_name, step in wf.steps.items():
        if step.tool not in SKILL_TOOLS:
            continue
        skill_cmd = step.with_args.get("skill_command", "")
        if not any(s in skill_cmd for s in _MULTIPART_SKILLS):
            continue
        if "plan_parts" not in step.capture_list:
            findings.append(
                RuleFinding(
                    rule="multipart-plan-parts-not-captured",
                    severity=Severity.ERROR,
                    step_name=step_name,
                    message=(
                        f"Step '{step_name}' calls a multi-part skill but does not capture "
                        f"'plan_parts' via capture_list. Add: "
                        f'capture_list:\\n  plan_parts: "${{{{ result.plan_parts }}}}" '
                        f"so the full ordered list of part files is in pipeline context."
                    ),
                )
            )

    return findings


@semantic_rule(
    name="skill-command-missing-prefix",
    description="run_skill step has a skill_command that does not start with '/'",
    severity=Severity.WARNING,
)
def _check_skill_command_prefix(ctx: ValidationContext) -> list[RuleFinding]:
    wf = ctx.recipe
    findings = []
    for step_name, step in wf.steps.items():
        if step.tool not in SKILL_TOOLS:
            continue
        skill_cmd = step.with_args.get("skill_command")
        if skill_cmd is None:
            continue  # absent key — fail-open
        if not skill_cmd.strip().startswith(SKILL_COMMAND_PREFIX):
            findings.append(
                RuleFinding(
                    rule="skill-command-missing-prefix",
                    severity=Severity.WARNING,
                    step_name=step_name,
                    message=(
                        f"skill_command {skill_cmd!r} does not start with '/'. "
                        "run_skill requires a slash-prefix (e.g. /autoskillit:investigate). "
                        "Prose prompts bypass the skill contract and run with "
                        "--dangerously-skip-permissions."
                    ),
                )
            )
    return findings


@semantic_rule(
    name="push-missing-explicit-remote-url",
    description="push_to_remote missing remote_url; implicit lookup fails for non-bare repos",
    severity=Severity.WARNING,
)
def _check_push_missing_explicit_remote_url(ctx: ValidationContext) -> list[RuleFinding]:
    recipe = ctx.recipe
    return [
        RuleFinding("push-missing-explicit-remote-url", Severity.WARNING, n, "missing remote_url")
        for n, step in recipe.steps.items()
        if step.tool == "push_to_remote" and "remote_url" not in (step.with_args or {})
    ]
