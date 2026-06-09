"""Semantic rules for model field adequacy on recipe steps.

Validates that context-intensive steps declare an explicit model rather
than relying on the default fallthrough, which may select a model with
an inadequate context window.
"""

from __future__ import annotations

from autoskillit.core import Severity
from autoskillit.recipe._analysis import ValidationContext
from autoskillit.recipe.registry import RuleFinding, make_finding, semantic_rule


@semantic_rule(
    name="model-empty-string-on-context-intensive-step",
    description=(
        "Steps that use dispatch_items (fanning out over multiple items in a single "
        "L1 session) must declare an explicit model with an adequate context window. "
        "An empty model falls through to config.model.default_model, which may be "
        "insufficient for the accumulated results."
    ),
    severity=Severity.WARNING,
)
def _check_model_empty_on_context_intensive(ctx: ValidationContext) -> list[RuleFinding]:
    recipe = ctx.recipe
    findings: list[RuleFinding] = []

    for step_name, step in recipe.steps.items():
        if step.tool not in ("run_skill", "run_python"):
            continue
        if step.action == "stop":
            continue
        if "dispatch_items" not in step.with_args:
            continue
        if step.model and step.model.strip():
            continue
        findings.append(
            make_finding(
                rule_name="model-empty-string-on-context-intensive-step",
                step_name=step_name,
                message=(
                    f"Step '{step_name}' uses dispatch_items but has no explicit model. "
                    f"An empty model falls through to config.model.default_model, which may "
                    f"have an inadequate context window for dispatching over multiple items."
                ),
            )
        )
    return findings
