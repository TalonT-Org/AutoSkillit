"""Campaign dispatch ingredient validation rules."""

from __future__ import annotations

from typing import TYPE_CHECKING

from autoskillit.core import Severity
from autoskillit.recipe._analysis import ValidationContext
from autoskillit.recipe._rule_helpers import _load_dispatch_target
from autoskillit.recipe.registry import RuleFinding, semantic_rule
from autoskillit.recipe.schema import RecipeKind

if TYPE_CHECKING:
    from autoskillit.recipe.schema import Recipe

# _run_dispatch() auto-injects these from dispatch-level fields (e.g. task: from the
# dispatch task: field), so campaigns that rely on this injection pattern should not
# be flagged for not explicitly forwarding them in the ingredients block.
_AUTO_INJECTED_CAMPAIGN_INGREDIENTS: frozenset[str] = frozenset({"task"})


@semantic_rule(
    name="dispatch-ingredients-keys-in-target-schema",
    description="Dispatch ingredient keys must exist in the target recipe's ingredients",
    severity=Severity.ERROR,
)
def _check_dispatch_ingredients_keys_in_target_schema(ctx: ValidationContext) -> list[RuleFinding]:
    if ctx.recipe.kind != RecipeKind.CAMPAIGN:
        return []
    findings: list[RuleFinding] = []
    for d in ctx.recipe.dispatches:
        if d.gate:
            continue
        if not d.ingredients:
            continue
        target = _load_dispatch_target(d, ctx.project_dir)
        if target is None:
            continue
        for key in d.ingredients:
            if key not in target.ingredients:
                findings.append(
                    RuleFinding(
                        rule="dispatch-ingredients-keys-in-target-schema",
                        severity=Severity.ERROR,
                        step_name="(top-level)",
                        message=(
                            f"Dispatch {d.name!r} passes ingredient {key!r} to recipe "
                            f"{d.recipe!r}, but that recipe does not declare ingredient {key!r}. "
                            f"Known ingredients: {sorted(target.ingredients)}"
                        ),
                    )
                )
    return findings


@semantic_rule(
    name="campaign-dangling-ingredient",
    description=(
        "Campaign ingredients should be forwarded to dispatches whose target recipe declares them"
    ),
    severity=Severity.WARNING,
)
def _check_campaign_dangling_ingredient(ctx: ValidationContext) -> list[RuleFinding]:
    if ctx.recipe.kind != RecipeKind.CAMPAIGN:
        return []
    campaign_ingredients = set(ctx.recipe.ingredients.keys())
    if not campaign_ingredients:
        return []
    findings: list[RuleFinding] = []
    for d in ctx.recipe.dispatches:
        # Gated dispatches are conditional — they may not run at all, so requiring
        # them to forward every campaign ingredient would produce false positives.
        if d.gate:
            continue
        target = _load_dispatch_target(d, ctx.project_dir)
        if target is None:
            continue
        forwarded_keys = set(d.ingredients.keys())
        for ing_name in campaign_ingredients:
            if ing_name in _AUTO_INJECTED_CAMPAIGN_INGREDIENTS:
                continue
            if ing_name in target.ingredients and ing_name not in forwarded_keys:
                findings.append(
                    RuleFinding(
                        rule="campaign-dangling-ingredient",
                        severity=Severity.WARNING,
                        step_name="(top-level)",
                        message=(
                            f"Campaign ingredient {ing_name!r} is declared by target "
                            f"recipe {d.recipe!r} (dispatch {d.name!r}) but is not "
                            f"forwarded in the dispatch's ingredients block. The sub-recipe "
                            f"will use its own default instead of the campaign-level value."
                        ),
                    )
                )
    return findings


@semantic_rule(
    name="dispatch-required-ingredient-provided",
    description=(
        "Target recipe required ingredients (no default) must be provided by the dispatch"
    ),
    severity=Severity.ERROR,
)
def _check_dispatch_required_ingredient_provided(
    ctx: ValidationContext,
) -> list[RuleFinding]:
    if ctx.recipe.kind != RecipeKind.CAMPAIGN:
        return []
    findings: list[RuleFinding] = []
    for d in ctx.recipe.dispatches:
        if d.gate:
            continue
        target = _load_dispatch_target(d, ctx.project_dir)
        if target is None:
            continue
        effective_ingredients = set(d.ingredients.keys())
        for auto in _AUTO_INJECTED_CAMPAIGN_INGREDIENTS:
            effective_ingredients.add(auto)
        for key, ing in target.ingredients.items():
            if ing.required and ing.default is None and key not in effective_ingredients:
                findings.append(
                    RuleFinding(
                        rule="dispatch-required-ingredient-provided",
                        severity=Severity.ERROR,
                        step_name="(top-level)",
                        message=(
                            f"Dispatch {d.name!r} targets recipe {d.recipe!r} which "
                            f"declares ingredient {key!r} as required (no default), "
                            f"but the dispatch does not provide it. "
                            f"Provided: {sorted(d.ingredients)}."
                        ),
                    )
                )
    return findings


@semantic_rule(
    name="dispatch-ingredient-values-are-strings",
    description="All dispatch ingredient values must be strings",
    severity=Severity.ERROR,
)
def _check_dispatch_ingredient_values_are_strings(ctx: ValidationContext) -> list[RuleFinding]:
    if ctx.recipe.kind != RecipeKind.CAMPAIGN:
        return []
    findings: list[RuleFinding] = []
    for d in ctx.recipe.dispatches:
        for key, val in d.ingredients.items():
            if not isinstance(val, str):
                findings.append(
                    RuleFinding(
                        rule="dispatch-ingredient-values-are-strings",
                        severity=Severity.ERROR,
                        step_name="(top-level)",
                        message=(
                            f"Dispatch {d.name!r} ingredient {key!r} has non-string value "
                            f"{val!r} ({type(val).__name__}). YAML auto-coercion detected — "
                            "quote the value in YAML."
                        ),
                    )
                )
    return findings
