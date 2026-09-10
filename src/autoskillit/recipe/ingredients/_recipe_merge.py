"""Sub-recipe merge logic — extracted from _recipe_composition (#4671 Phase D).

Holds the ``_merge_sub_recipe`` helper that was carved out of
``_recipe_composition.py`` to bring the file under REQ-CNST-010's 750-line
diff-scoped hard cap. The helper rewrites sub-recipe routes (``done`` /
``escalate`` / intra-sub-recipe step names), injects the prefixed sub-recipe
steps in place of the parent placeholder, and unions ingredients,
``kitchen_rules``, ``requires_packs``, and ``requires_features`` from the
sub-recipe into the parent.
"""

from __future__ import annotations

import dataclasses
from typing import TYPE_CHECKING, Any

import regex as re

from autoskillit.recipe.schema import (
    StepResultCondition,
    StepResultRoute,
)

if TYPE_CHECKING:
    # Lazy / TYPE_CHECKING-only — avoids the circular import:
    # ``_recipe_composition`` imports this module to access ``_merge_sub_recipe``.
    pass

__all__ = ["_merge_sub_recipe"]


def _merge_sub_recipe(parent: Any, placeholder_name: str, sub: Any) -> Any:
    """Replace the sub_recipe placeholder step with the sub-recipe's steps.

    Algorithm:
    1. Compute a safe name prefix from the sub-recipe name.
    2. For each step in sub, create a prefixed copy with routing fixed:
       - Routes to "done" → parent placeholder's on_success
       - Routes to "escalate" → parent placeholder's on_failure
       - Routes to other sub-recipe step names → add prefix
    3. Insert sub-recipe steps in place of the placeholder.
    4. Merge ingredients: add sub-recipe's non-hidden ingredients into parent.
    5. Merge kitchen_rules: union (deduplicated), sub-recipe rules appended.
    """
    # Lazy import to break the ``_recipe_composition`` ↔ ``_recipe_merge`` cycle.
    from autoskillit.recipe.ingredients._recipe_composition import (
        _rewrite_step_routes,
    )

    if placeholder_name not in parent.steps:
        raise KeyError(
            f"_merge_sub_recipe: placeholder step '{placeholder_name}' not found in "
            f"recipe '{parent.name}'. Available steps: {list(parent.steps.keys())}"
        )
    placeholder = parent.steps[placeholder_name]
    on_success = placeholder.on_success or "done"
    on_failure = placeholder.on_failure or "escalate"

    # Build prefix: "sprint-prefix" → "sprint_prefix_", "my-sub" → "my_sub_"
    raw_prefix = re.sub(r"[^a-z0-9]", "_", (sub.name or placeholder_name).lower())
    if not raw_prefix.endswith("_"):
        raw_prefix += "_"
    prefix = raw_prefix

    sub_step_names = set(sub.steps.keys())

    def _fix_route(target: str | None) -> str | None:
        if target is None:
            return None
        if target == "done":
            return on_success
        if target == "escalate":
            return on_failure
        if target in sub_step_names:
            return prefix + target
        return target

    def _fix_result_route(route: Any) -> Any:
        if route is None:
            return None
        if route.conditions:
            return StepResultRoute(
                conditions=[
                    StepResultCondition(when=c.when, route=_fix_route(c.route) or "")
                    for c in route.conditions
                ]
            )
        return StepResultRoute(
            field=route.field,
            routes={k: (_fix_route(v) or v) for k, v in route.routes.items()},
        )

    prefixed_steps: dict[str, Any] = {}
    for sub_step_name, sub_step in sub.steps.items():
        new_name = prefix + sub_step_name
        new_step = dataclasses.replace(
            sub_step,
            on_success=_fix_route(sub_step.on_success),
            on_failure=_fix_route(sub_step.on_failure),
            on_context_limit=_fix_route(sub_step.on_context_limit),
            on_rate_limit=_fix_route(sub_step.on_rate_limit),
            on_exhausted=_fix_route(sub_step.on_exhausted),
            on_skip=_fix_route(sub_step.on_skip),
            on_result=_fix_result_route(sub_step.on_result),
        )
        prefixed_steps[new_name] = new_step

    # Assemble new steps dict: sub-recipe steps injected in place of placeholder
    new_steps: dict[str, Any] = {}
    for step_name, step in parent.steps.items():
        if step_name == placeholder_name:
            new_steps.update(prefixed_steps)
        else:
            new_steps[step_name] = _rewrite_step_routes(
                step, {placeholder_name: next(iter(prefixed_steps))}
            )

    # Merge ingredients: sub-recipe non-hidden ingredients into parent
    merged_ingredients = dict(parent.ingredients)
    for ing_name, ing in sub.ingredients.items():
        if getattr(ing, "hidden", False):
            continue  # do not propagate hidden sub-recipe ingredients to parent
        if ing_name not in merged_ingredients:
            merged_ingredients[ing_name] = ing

    # Merge kitchen_rules: union (parent first, then sub-recipe additions)
    seen_rules: set[str] = set(parent.kitchen_rules)
    merged_rules = list(parent.kitchen_rules)
    for rule in sub.kitchen_rules:
        if rule not in seen_rules:
            merged_rules.append(rule)
            seen_rules.add(rule)

    # Merge requires_packs: union (parent first, then sub-recipe additions)
    seen_packs: set[str] = set(parent.requires_packs)
    merged_packs = list(parent.requires_packs)
    for pack in sub.requires_packs:
        if pack not in seen_packs:
            merged_packs.append(pack)
            seen_packs.add(pack)

    # Merge requires_features: union (parent first, then sub-recipe additions)
    seen_features: set[str] = set(parent.requires_features)
    merged_features = list(parent.requires_features)
    for feat in sub.requires_features:
        if feat not in seen_features:
            merged_features.append(feat)
            seen_features.add(feat)

    return dataclasses.replace(
        parent,
        steps=new_steps,
        ingredients=merged_ingredients,
        kitchen_rules=merged_rules,
        requires_packs=merged_packs,
        requires_features=merged_features,
    )
