"""Semantic validation rules — loop artifact scope enforcement."""

from __future__ import annotations

from autoskillit.core import SKILL_TOOLS, Severity
from autoskillit.recipe._analysis import ValidationContext
from autoskillit.recipe._rule_helpers import _find_cycle_members
from autoskillit.recipe.registry import RuleFinding, semantic_rule


@semantic_rule(
    name="loop-iterated-step-requires-iteration-scoped-output",
    description=(
        "run_skill steps inside routing cycles must use iteration-scoped output_dir "
        "to prevent artifact collision between loop iterations"
    ),
    severity=Severity.ERROR,
)
def _check_loop_artifact_scope(ctx: ValidationContext) -> list[RuleFinding]:
    """Flag run_skill steps in cycles that have a static (non-iteration-scoped) output_dir."""
    findings: list[RuleFinding] = []
    recipe_steps = ctx.recipe.steps

    cycle_sets = _find_cycle_members(ctx.step_graph, recipe_steps)

    for cycle_set in cycle_sets:
        for step_name in cycle_set:
            step = recipe_steps.get(step_name)
            if step is None:
                continue
            if step.tool not in SKILL_TOOLS:
                continue
            output_dir = step.with_args.get("output_dir", "")
            if not output_dir:
                continue
            if "{{AUTOSKILLIT_TEMP}}" not in output_dir:
                continue
            if "${{ context." in output_dir:
                continue
            cycle_list = sorted(cycle_set)
            findings.append(
                RuleFinding(
                    rule="loop-iterated-step-requires-iteration-scoped-output",
                    severity=Severity.ERROR,
                    step_name=step_name,
                    message=(
                        f"Step '{step_name}' is in a loop cycle but uses a static "
                        f"output_dir '{output_dir}'. Loop-iterated run_skill steps must "
                        f"include an iteration-scoped context variable in output_dir "
                        f"(e.g., '${{{{ context.review_loop_count }}}}') to prevent "
                        f"artifact collision. Cycle: [{'→'.join(cycle_list)}]"
                    ),
                )
            )

    return findings
