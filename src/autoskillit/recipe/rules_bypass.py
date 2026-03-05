"""Semantic validation rules for skip_when_false bypass routing contracts."""

from __future__ import annotations

from autoskillit.core import Severity
from autoskillit.recipe._analysis import ValidationContext
from autoskillit.recipe.registry import RuleFinding, semantic_rule


@semantic_rule(
    name="optional-without-skip-when",
    description=(
        "A step marked optional: true has no skip_when_false declaration. "
        "The bypass route is invisible to static analysis and the step graph."
    ),
    severity=Severity.ERROR,
)
def _check_optional_without_skip_when(ctx: ValidationContext) -> list[RuleFinding]:
    wf = ctx.recipe
    findings = []
    for name, step in wf.steps.items():
        if step.optional and not step.skip_when_false:
            findings.append(
                RuleFinding(
                    rule="optional-without-skip-when",
                    severity=Severity.ERROR,
                    step_name=name,
                    message=(
                        f"Step '{name}' is optional: true but has no skip_when_false. "
                        "Declare skip_when_false: 'inputs.<ingredient>' to make the "
                        "bypass route machine-verifiable and include it in the step graph."
                    ),
                )
            )
    return findings


@semantic_rule(
    name="skip-when-false-undeclared",
    description=(
        "skip_when_false references an ingredient name that is not declared "
        "in the recipe's ingredients section."
    ),
    severity=Severity.ERROR,
)
def _check_skip_when_false_undeclared(ctx: ValidationContext) -> list[RuleFinding]:
    wf = ctx.recipe
    findings = []
    declared_ingredients = set(wf.ingredients.keys())
    for name, step in wf.steps.items():
        if not step.skip_when_false:
            continue
        ref = step.skip_when_false
        if not ref.startswith("inputs."):
            findings.append(
                RuleFinding(
                    rule="skip-when-false-undeclared",
                    severity=Severity.ERROR,
                    step_name=name,
                    message=(
                        f"Step '{name}': skip_when_false '{ref}' must use 'inputs.<name>' format."
                    ),
                )
            )
            continue
        ingredient_name = ref[len("inputs.") :]
        if ingredient_name not in declared_ingredients:
            findings.append(
                RuleFinding(
                    rule="skip-when-false-undeclared",
                    severity=Severity.ERROR,
                    step_name=name,
                    message=(
                        f"Step '{name}': skip_when_false references undeclared ingredient "
                        f"'{ingredient_name}'. Add it to the recipe's ingredients section."
                    ),
                )
            )
    return findings
