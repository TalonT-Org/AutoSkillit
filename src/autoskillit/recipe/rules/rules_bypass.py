"""Semantic validation rules for skip_when_false bypass routing contracts."""

from __future__ import annotations

from autoskillit.core import SKILL_TOOLS, Severity
from autoskillit.recipe._analysis import ValidationContext
from autoskillit.recipe._recipe_composition import _is_ingredient_truthy
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


@semantic_rule(
    name="advisory-step-missing-context-limit",
    description=(
        "A run_skill step with skip_when_false declared (advisory/optional) must define "
        "on_context_limit. The toggle skip_when_false says the step may be skipped by config; "
        "the absence of on_context_limit means it cannot be skipped on context exhaustion — "
        "an inconsistency in the skippability contract."
    ),
    severity=Severity.WARNING,
)
def _advisory_step_missing_context_limit(ctx: ValidationContext) -> list[RuleFinding]:
    findings: list[RuleFinding] = []
    for step_name, step in ctx.recipe.steps.items():
        if step.tool not in SKILL_TOOLS:
            continue
        if step.skip_when_false is None:
            continue
        if step.on_context_limit is not None:
            continue
        findings.append(
            RuleFinding(
                rule="advisory-step-missing-context-limit",
                severity=Severity.WARNING,
                step_name=step_name,
                message=(
                    f"Step '{step_name}' is advisory (skip_when_false={step.skip_when_false!r}) "
                    f"but declares no on_context_limit. A step that can be skipped by "
                    f"configuration must also handle context exhaustion gracefully. "
                    f"Set on_context_limit to the same target as on_success to skip the "
                    f"advisory step on context limit."
                ),
            )
        )
    return findings


@semantic_rule(
    name="skip-when-false-on-hidden",
    description=(
        "A step uses skip_when_false referencing a hidden ingredient. "
        "Hidden ingredients are invisible to the LLM. This works because "
        "skip_when_false is evaluated server-side, but the pattern is fragile — "
        "prefer gate: on a sub_recipe step for hidden ingredient gating."
    ),
    severity=Severity.WARNING,
)
def _check_skip_when_false_on_hidden(ctx: ValidationContext) -> list[RuleFinding]:
    findings: list[RuleFinding] = []
    for step_name, step in ctx.recipe.steps.items():
        if not step.skip_when_false:
            continue
        ref = step.skip_when_false
        if not ref.startswith("inputs."):
            continue
        ingredient_name = ref[len("inputs.") :]
        ing = ctx.recipe.ingredients.get(ingredient_name)
        if ing is not None and ing.hidden:
            findings.append(
                RuleFinding(
                    rule="skip-when-false-on-hidden",
                    severity=Severity.WARNING,
                    step_name=step_name,
                    message=(
                        f"Step '{step_name}': skip_when_false references hidden ingredient "
                        f"'{ingredient_name}'. Hidden ingredients are invisible to the LLM. "
                        "This works because skip_when_false is evaluated server-side, but the "
                        "pattern is fragile — prefer gate: on a sub_recipe step for hidden "
                        "ingredient gating."
                    ),
                )
            )
    return findings


@semantic_rule(
    name="skip-when-false-on-non-boolean",
    description=(
        "skip_when_false references an ingredient whose default is not in "
        "{'true', 'false'}, indicating it carries a non-boolean value (URL, path, etc.). "
        "skip_when_false evaluates presence via truthiness — any non-empty, non-falsy "
        "string is truthy. This is correct behavior but differs from the boolean "
        "true/false pattern most skip_when_false guards use."
    ),
    severity=Severity.WARNING,
)
def _check_skip_when_false_on_non_boolean(ctx: ValidationContext) -> list[RuleFinding]:
    findings: list[RuleFinding] = []
    for step_name, step in ctx.recipe.steps.items():
        if not step.skip_when_false:
            continue
        ref = step.skip_when_false
        if not ref.startswith("inputs."):
            continue
        ingredient_name = ref[len("inputs.") :]
        ing = ctx.recipe.ingredients.get(ingredient_name)
        if ing is None:
            continue
        if ing.default in ("true", "false"):
            continue
        if ing.required and ing.default is None:
            continue
        findings.append(
            RuleFinding(
                rule="skip-when-false-on-non-boolean",
                severity=Severity.WARNING,
                step_name=step_name,
                message=(
                    f"Step '{step_name}': skip_when_false references '{ingredient_name}' "
                    f"which is not a boolean ingredient (default: {ing.default!r}). "
                    "Truthiness evaluation treats any non-empty, non-falsy string as "
                    "truthy (presence check). Verify this is the intended behavior."
                ),
            )
        )
    return findings


@semantic_rule(
    name="skip-guard-falsy-default",
    description=(
        "skip_when_false references an ingredient with a falsy or absent default. "
        "In interactive cook sessions, this step will be deferred until the user "
        "provides an override value."
    ),
    severity=Severity.WARNING,
)
def _check_skip_guard_falsy_default(ctx: ValidationContext) -> list[RuleFinding]:
    findings: list[RuleFinding] = []
    for step_name, step in ctx.recipe.steps.items():
        if not step.skip_when_false or not step.skip_when_false.startswith("inputs."):
            continue
        ingredient_name = step.skip_when_false[len("inputs.") :]
        ing = ctx.recipe.ingredients.get(ingredient_name)
        default = ing.default if ing else None
        if default is None or not _is_ingredient_truthy(str(default)):
            findings.append(
                RuleFinding(
                    rule="skip-guard-falsy-default",
                    severity=Severity.WARNING,
                    step_name=step_name,
                    message=(
                        f"Step '{step_name}': skip_when_false references '{ingredient_name}' "
                        f"with falsy default {default!r}. In interactive sessions, this step "
                        f"will be deferred until the user provides an override."
                    ),
                )
            )
    return findings
