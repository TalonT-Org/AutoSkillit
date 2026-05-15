"""Semantic validation rules for route-gate steps."""

from __future__ import annotations

from autoskillit.core import Severity
from autoskillit.recipe._analysis import ValidationContext
from autoskillit.recipe.registry import RuleFinding, semantic_rule
from autoskillit.recipe.schema import RecipeKind


def _reachable_stops(ctx: ValidationContext, target: str) -> set[str]:
    """Return non-escalation stop steps reachable from target without crossing route gates."""
    target_step = ctx.recipe.steps.get(target)
    if target_step is not None and target_step.action == "route":
        return set()
    graph = ctx.step_graph
    visited: set[str] = {target}
    queue: list[str] = [target]
    stops: set[str] = set()
    while queue:
        node = queue.pop()
        step = ctx.recipe.steps.get(node)
        if step is None:
            continue
        if step.action == "stop" and not node.startswith("escalate"):
            stops.add(node)
        if step.action == "route":
            continue
        for neighbor in graph.get(node, set()):
            if neighbor not in visited:
                visited.add(neighbor)
                queue.append(neighbor)
    return stops


@semantic_rule(
    name="route-gate-shared-stop",
    description="Route gate fallback and primary paths share a non-escalation stop step.",
    severity=Severity.WARNING,
)
def _check_route_gate_shared_stop(ctx: ValidationContext) -> list[RuleFinding]:
    if ctx.recipe.kind == RecipeKind.CAMPAIGN:
        return []

    findings: list[RuleFinding] = []

    for step_name, step in ctx.recipe.steps.items():
        if step.action != "route":
            continue
        if step.on_result is None:
            continue
        if step.on_result.field or step.on_result.routes:
            continue
        conditions = step.on_result.conditions
        if not conditions:
            continue

        fallback_conds = [c for c in conditions if c.when is None]
        primary_conds = [c for c in conditions if c.when is not None]

        if not fallback_conds or not primary_conds:
            continue

        fallback_stops = _reachable_stops(ctx, fallback_conds[0].route)

        for primary in primary_conds:
            primary_stops = _reachable_stops(ctx, primary.route)
            shared = fallback_stops & primary_stops
            for stop_name in shared:
                findings.append(
                    RuleFinding(
                        rule="route-gate-shared-stop",
                        severity=Severity.WARNING,
                        step_name=step_name,
                        message=(
                            f"Route gate '{step_name}' fallback path and primary path "
                            f"(when={primary.when!r}) both reach the same non-escalation "
                            f"stop step '{stop_name}'. Use distinct stop steps with "
                            f"path-appropriate messages."
                        ),
                    )
                )

    return findings
