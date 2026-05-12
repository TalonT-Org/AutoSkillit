"""Semantic validation rules for food-truck recipes."""

from __future__ import annotations

from autoskillit.core import Severity
from autoskillit.recipe._analysis import ValidationContext
from autoskillit.recipe.registry import RuleFinding, semantic_rule
from autoskillit.recipe.schema import RecipeKind


@semantic_rule(
    name="food-truck-has-sentinel-stop",
    description="Food-truck recipes must have a stop step referencing L3 sentinel",
    severity=Severity.ERROR,
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
            severity=Severity.ERROR,
            step_name="(top-level)",
            message=(
                "Food-truck recipe has no stop step referencing L3 sentinel. "
                "Food trucks must emit a sentinel JSON block on completion."
            ),
        )
    ]


@semantic_rule(
    name="escalate-route-coverage",
    description="Warn when food-truck recipes use escalate routing targets",
    severity=Severity.WARNING,
)
def _check_food_truck_escalate_route_coverage(
    ctx: ValidationContext,
) -> list[RuleFinding]:
    if ctx.recipe.kind != RecipeKind.FOOD_TRUCK:
        return []
    escalate_routes: list[str] = []
    for step_name, step in ctx.recipe.steps.items():
        for target in [step.on_success, step.on_failure]:
            if target and "escalate" in target.lower():
                escalate_routes.append(f"{step_name} → {target}")
        if step.on_result and "escalate" in str(step.on_result).lower():
            escalate_routes.append(f"{step_name} → on_result(escalate)")
    if not escalate_routes:
        return []
    return [
        RuleFinding(
            rule="escalate-route-coverage",
            severity=Severity.WARNING,
            step_name="(top-level)",
            message=(
                f"Food-truck recipe uses escalate routing in: {', '.join(escalate_routes)}. "
                "Escalate routes depend on LLM routing compliance. "
                "The fleet prompt's ROUTING AUTHORITY clause is the enforcement mechanism."
            ),
        )
    ]
