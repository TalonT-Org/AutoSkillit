"""Semantic rule: 1:1 gating ingredient ↔ step name asymmetry detection."""

from __future__ import annotations

from collections import defaultdict

from autoskillit.core import Severity
from autoskillit.recipe._analysis import ValidationContext
from autoskillit.recipe.registry import RuleFinding, semantic_rule

_DESCRIPTIVE_SUFFIXES = frozenset({"_enabled", "_mode", "_level", "_flag"})


@semantic_rule(
    name="ingredient-step-name-asymmetry",
    description=(
        "A 1:1 gating ingredient's name does not match the single step "
        "it gates via skip_when_false, creating orchestrator confusion."
    ),
    severity=Severity.WARNING,
)
def _check_ingredient_step_name_asymmetry(ctx: ValidationContext) -> list[RuleFinding]:
    ing_to_steps: dict[str, list[str]] = defaultdict(list)
    for step_name, step in ctx.recipe.steps.items():
        swf = getattr(step, "skip_when_false", None)
        if not swf:
            continue
        if not swf.startswith("inputs."):
            continue
        ing_name = swf[len("inputs.") :]
        ing_to_steps[ing_name].append(step_name)

    findings: list[RuleFinding] = []
    for ing_name, step_names in ing_to_steps.items():
        if len(step_names) != 1:
            continue
        step_name = step_names[0]
        if ing_name == step_name:
            continue
        if any(ing_name.endswith(suffix) for suffix in _DESCRIPTIVE_SUFFIXES):
            continue
        findings.append(
            RuleFinding(
                rule="ingredient-step-name-asymmetry",
                severity=Severity.WARNING,
                step_name=step_name,
                message=(
                    f"Ingredient '{ing_name}' gates only step '{step_name}' "
                    f"via skip_when_false, but their names differ. "
                    f"Rename the ingredient to '{step_name}' to match "
                    f"the step name and prevent orchestrator confusion."
                ),
            )
        )
    return findings
