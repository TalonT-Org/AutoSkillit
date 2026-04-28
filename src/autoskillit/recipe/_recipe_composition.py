"""Sub-recipe composition: merge sub-recipes into parent recipe."""

from __future__ import annotations

import dataclasses
import re
from pathlib import Path
from typing import Any

from autoskillit.core import YAMLError
from autoskillit.recipe.io import find_sub_recipe_by_name
from autoskillit.recipe.io import load_recipe as _load_recipe_from_path
from autoskillit.recipe.schema import Recipe, StepResultCondition, StepResultRoute  # noqa: F401


def _drop_sub_recipe_step(recipe: Any, step_name: str) -> Any:
    """Return a new Recipe with the named sub_recipe placeholder step removed."""
    new_steps = {k: v for k, v in recipe.steps.items() if k != step_name}
    return dataclasses.replace(recipe, steps=new_steps)


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
            on_exhausted=_fix_route(sub_step.on_exhausted),
            on_result=_fix_result_route(sub_step.on_result),
        )
        prefixed_steps[new_name] = new_step

    # Assemble new steps dict: sub-recipe steps injected in place of placeholder
    new_steps: dict[str, Any] = {}
    for step_name, step in parent.steps.items():
        if step_name == placeholder_name:
            new_steps.update(prefixed_steps)
        else:
            new_steps[step_name] = step

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

    return dataclasses.replace(
        parent,
        steps=new_steps,
        ingredients=merged_ingredients,
        kitchen_rules=merged_rules,
        requires_packs=merged_packs,
    )


def _build_active_recipe(
    recipe: Any,
    ingredient_overrides: dict[str, str] | None,
    project_dir: Path,
    temp_dir_relpath: str = ".autoskillit/temp",
) -> tuple[Any, Any | None]:
    """Return (active_recipe, combined_recipe | None).

    active_recipe: the Recipe to serve to the agent.
        - If no sub_recipe steps: returns recipe unchanged.
        - If sub_recipe step with gate=false: returns recipe with sub_recipe step dropped.
        - If sub_recipe step with gate=true: returns the merged (combined) recipe.

    combined_recipe: the merged Recipe if any gate was true, else None.
        Used to run dual validation (REQ-VALID-004).
    """
    overrides = ingredient_overrides or {}
    sub_recipe_steps = [
        (name, step) for name, step in recipe.steps.items() if step.sub_recipe is not None
    ]
    if not sub_recipe_steps:
        return recipe, None

    combined: Any | None = None
    working = recipe

    # Re-read each step from working.steps to get the current state after prior
    # merge/drop operations, rather than using the stale reference from recipe.steps.
    for step_name, _orig_step in sub_recipe_steps:
        current_step = working.steps.get(step_name)
        if current_step is None or current_step.sub_recipe is None:
            continue
        gate_name = current_step.gate or ""
        gate_ingredient = working.ingredients.get(gate_name) if gate_name else None
        gate_default: str = (gate_ingredient.default or "false") if gate_ingredient else "false"
        gate_value = overrides.get(gate_name, gate_default)

        if gate_value.lower() in ("true", "1", "yes"):
            sr_path = find_sub_recipe_by_name(current_step.sub_recipe, project_dir)
            if sr_path is None:
                raise FileNotFoundError(
                    f"Sub-recipe '{current_step.sub_recipe}' not found. "
                    f"Expected in recipes/sub-recipes/{current_step.sub_recipe}.yaml"
                )
            try:
                sub_recipe = _load_recipe_from_path(sr_path, temp_dir_relpath)
            except (YAMLError, ValueError, OSError) as exc:
                raise ValueError(
                    f"Failed to load sub-recipe '{current_step.sub_recipe}' "
                    f"(gate: {gate_name}={gate_value}): {exc}"
                ) from exc
            working = _merge_sub_recipe(working, step_name, sub_recipe)
            combined = working
        else:
            working = _drop_sub_recipe_step(working, step_name)

    return working, combined
