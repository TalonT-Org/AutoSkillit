"""Semantic validation rules for food-truck recipes."""

from __future__ import annotations

from autoskillit.core import Severity
from autoskillit.recipe._analysis import ValidationContext
from autoskillit.recipe.registry import RuleFinding, semantic_rule
from autoskillit.recipe.schema import RecipeKind


@semantic_rule(
    name="food-truck-has-sentinel-stop",
    description="Food-truck recipes must have a stop step referencing L3 sentinel",
    severity=Severity.WARNING,
)
def _check_food_truck_has_sentinel_stop(ctx: ValidationContext) -> list[RuleFinding]:
    if ctx.recipe.kind != RecipeKind.FOOD_TRUCK:
        return []
    for step_name, step in ctx.recipe.steps.items():
        if step.action == "stop" and step.message and "sentinel" in step.message.lower():
            return []
    return [
        RuleFinding(
            rule="food-truck-has-sentinel-stop",
            severity=Severity.WARNING,
            step_name="(top-level)",
            message=(
                "Food-truck recipe has no stop step referencing L3 sentinel. "
                "Food trucks must emit a sentinel JSON block on completion."
            ),
        )
    ]
