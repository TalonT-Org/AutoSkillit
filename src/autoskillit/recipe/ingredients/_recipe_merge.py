"""Sub-recipe merge logic — extracted from _recipe_composition.

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
from typing import Any

import regex as re

__all__ = ["_merge_sub_recipe"]


def _append_unseen_strings(parent: list[str], additions: list[str]) -> list[str]:
    merged = list(parent)
    seen = set(merged)
    for addition in additions:
        if addition not in seen:
            merged.append(addition)
            seen.add(addition)
    return merged


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

    redirects = {name: prefix + name for name in sub.steps}
    redirects["done"] = on_success
    redirects["escalate"] = on_failure

    prefixed_steps: dict[str, Any] = {}
    for sub_step_name, sub_step in sub.steps.items():
        new_name = prefix + sub_step_name
        prefixed_steps[new_name] = _rewrite_step_routes(sub_step, redirects)

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

    return dataclasses.replace(
        parent,
        steps=new_steps,
        ingredients=merged_ingredients,
        kitchen_rules=_append_unseen_strings(parent.kitchen_rules, sub.kitchen_rules),
        requires_packs=_append_unseen_strings(parent.requires_packs, sub.requires_packs),
        requires_features=_append_unseen_strings(parent.requires_features, sub.requires_features),
    )
