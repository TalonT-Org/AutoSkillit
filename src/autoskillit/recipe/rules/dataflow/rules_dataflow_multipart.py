"""Semantic rules for multi-part recipe iteration checks."""

from __future__ import annotations

from autoskillit.core import SKILL_TOOLS, Severity, extract_skill_name
from autoskillit.recipe._analysis import ValidationContext
from autoskillit.recipe._skill_helpers import MULTIPART_SKILL_NAMES
from autoskillit.recipe.registry import RuleFinding, make_finding, semantic_rule


@semantic_rule(
    name="multipart-iteration-notes",
    description="Multi-part plan recipes must declare iteration conventions.",
    severity=Severity.ERROR,
)
def _check_multipart_iteration_notes(ctx: ValidationContext) -> list[RuleFinding]:
    wf = ctx.recipe
    findings: list[RuleFinding] = []

    has_multipart_step = any(
        step.tool in SKILL_TOOLS
        and extract_skill_name(step.with_args.get("skill_command", "")) in MULTIPART_SKILL_NAMES
        for step in wf.steps.values()
    )
    if not has_multipart_step:
        return []

    sequential_keywords = ("SEQUENTIAL EXECUTION", "full cycle", "Never run verify for all parts")
    rules_text = " ".join(wf.kitchen_rules)
    if not any(kw in rules_text for kw in sequential_keywords):
        findings.append(
            make_finding(
                rule_name="multipart-iteration-notes",
                step_name="kitchen_rules",
                message="Recipe uses make-plan or rectify but kitchen_rules do not contain "
                "a sequential execution constraint. Without it, agents may "
                "batch-verify all parts before "
                "implementing any. Add a rule such as: 'SEQUENTIAL EXECUTION: complete full "
                "cycle per part before advancing.'",
                severity=Severity.WARNING,
            )
        )

    next_or_done = wf.steps.get("next_or_done")
    if next_or_done is not None and next_or_done.on_result is not None:
        # Legacy format: field/routes dict with explicit "more_parts" → any step
        more_parts_target = next_or_done.on_result.routes.get("more_parts")
        has_more_parts_route = more_parts_target is not None and more_parts_target in wf.steps
        # Predicate format: condition with "more_parts" in the when clause routing to any step
        if not has_more_parts_route:
            has_more_parts_route = any(
                cond.route in wf.steps and cond.when is not None and "more_parts" in cond.when
                for cond in next_or_done.on_result.conditions
            )
        if not has_more_parts_route:
            findings.append(
                make_finding(
                    rule_name="multipart-iteration-notes",
                    step_name="next_or_done",
                    message="Recipe uses make-plan or rectify but next_or_done does not route "
                    "'more_parts' back to a recipe step. Sequential part processing requires "
                    "a more_parts → <loop-back-step> condition in the on_result routes.",
                )
            )

    return findings
