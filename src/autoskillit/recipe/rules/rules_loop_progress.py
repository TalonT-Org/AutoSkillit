"""Semantic validation rules — loop progress tracking enforcement."""

from __future__ import annotations

from autoskillit.core import (
    SKILL_TOOLS,
    Severity,
    get_logger,
    resolve_skill_name,
)
from autoskillit.recipe._analysis import ValidationContext
from autoskillit.recipe._rule_helpers import _find_cycle_members
from autoskillit.recipe.contracts import (
    get_skill_contract,
    load_bundled_manifest,
)
from autoskillit.recipe.registry import RuleFinding, semantic_rule

logger = get_logger(__name__)


@semantic_rule(
    name="loop-body-uncaptured-output",
    description=("run_skill steps inside routing cycles must capture declared outputs"),
    severity=Severity.ERROR,
)
def _check_loop_body_capture(ctx: ValidationContext) -> list[RuleFinding]:
    """Flag run_skill steps in cycles that have no capture block despite declared outputs."""
    findings: list[RuleFinding] = []
    manifest = load_bundled_manifest()
    recipe_steps = ctx.recipe.steps

    cycle_sets = _find_cycle_members(ctx.step_graph, recipe_steps)

    for cycle_set in cycle_sets:
        for step_name in cycle_set:
            step = recipe_steps.get(step_name)
            if step is None:
                continue
            if step.tool not in SKILL_TOOLS:
                continue

            skill_cmd = step.with_args.get("skill_command", "")
            name = resolve_skill_name(skill_cmd)
            if not name:
                continue

            contract = get_skill_contract(name, manifest)
            if not contract:
                continue

            if not contract.outputs:
                continue

            if step.capture is None or len(step.capture) == 0:
                cycle_list = sorted(cycle_set)
                findings.append(
                    RuleFinding(
                        rule="loop-body-uncaptured-output",
                        severity=Severity.ERROR,
                        step_name=step_name,
                        message=(
                            f"Step '{step_name}' in loop [{'→'.join(cycle_list)}] "
                            f"invokes skill '{name}' with declared outputs "
                            f"{[o.name for o in contract.outputs]} "
                            f"but has no capture: block"
                        ),
                    )
                )

    return findings
