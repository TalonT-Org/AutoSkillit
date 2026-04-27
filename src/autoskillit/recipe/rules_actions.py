"""Semantic validation rules for action-type steps (stop, route, confirm)."""

from __future__ import annotations

from autoskillit.core import Severity, get_logger
from autoskillit.recipe._analysis import ValidationContext
from autoskillit.recipe.registry import RuleFinding, semantic_rule
from autoskillit.recipe.schema import RecipeKind

_logger = get_logger(__name__)

_PLACEHOLDER_MESSAGES: frozenset[str] = frozenset(
    {"done", "ok", "complete", "finished", "end", "stop", "exit"}
)


@semantic_rule(
    name="stop-step-has-no-routing",
    description="Stop steps must not have outbound routing fields.",
    severity=Severity.ERROR,
)
def _check_stop_step_has_no_routing(ctx: ValidationContext) -> list[RuleFinding]:
    findings: list[RuleFinding] = []
    for step_name, step in ctx.recipe.steps.items():
        if step.action != "stop":
            continue
        has_routing = (
            step.on_success is not None
            or step.on_failure is not None
            or step.on_context_limit is not None
            or step.on_result is not None
            or step.on_exhausted != "escalate"
        )
        if has_routing:
            findings.append(
                RuleFinding(
                    rule="stop-step-has-no-routing",
                    severity=Severity.ERROR,
                    step_name=step_name,
                    message=(
                        f"Stop step '{step_name}' has outbound routing fields. "
                        "Stop steps are terminal — routing fields are contradictory "
                        "and indicate author confusion."
                    ),
                )
            )
    return findings


@semantic_rule(
    name="stop-step-message-quality",
    description="Stop step messages must be meaningful (≥10 characters and not a placeholder).",
    severity=Severity.WARNING,
)
def _check_stop_step_message_quality(ctx: ValidationContext) -> list[RuleFinding]:
    findings: list[RuleFinding] = []
    for step_name, step in ctx.recipe.steps.items():
        if step.action != "stop":
            continue
        msg = (step.message or "").strip()
        if len(msg) < 10 or msg.lower() in _PLACEHOLDER_MESSAGES:
            findings.append(
                RuleFinding(
                    rule="stop-step-message-quality",
                    severity=Severity.WARNING,
                    step_name=step_name,
                    message=(
                        f"Stop step '{step_name}' has a weak message: {msg!r}. "
                        "Use a meaningful description (≥10 characters) to give the "
                        "model a clear terminus signal."
                    ),
                )
            )
    return findings


@semantic_rule(
    name="recipe-has-terminal-step",
    description="Every recipe must have at least one stop step.",
    severity=Severity.ERROR,
)
def _check_recipe_has_terminal_step(ctx: ValidationContext) -> list[RuleFinding]:
    if ctx.recipe.kind == RecipeKind.CAMPAIGN:
        return []
    has_stop = any(step.action == "stop" for step in ctx.recipe.steps.values())
    if not has_stop:
        return [
            RuleFinding(
                rule="recipe-has-terminal-step",
                severity=Severity.ERROR,
                step_name="",
                message=(
                    "Recipe has no stop step. Every recipe must have at least one "
                    "terminal node — without one, the pipeline has no defined end state."
                ),
            )
        ]
    return []


@semantic_rule(
    name="route-step-requires-on-result",
    description="Route steps must have an on_result block to serve their purpose.",
    severity=Severity.WARNING,
)
def _check_route_step_requires_on_result(ctx: ValidationContext) -> list[RuleFinding]:
    findings: list[RuleFinding] = []
    for step_name, step in ctx.recipe.steps.items():
        if step.action != "route":
            continue
        if step.on_result is None:
            findings.append(
                RuleFinding(
                    rule="route-step-requires-on-result",
                    severity=Severity.WARNING,
                    step_name=step_name,
                    message=(
                        f"Route step '{step_name}' has no on_result block. "
                        "Route steps exist to make conditional routing decisions; "
                        "without on_result, they serve no purpose."
                    ),
                )
            )
    return findings
