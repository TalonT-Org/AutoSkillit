"""Sub-recipe composition: merge sub-recipes into parent recipe."""

from __future__ import annotations

import dataclasses
from pathlib import Path
from typing import Any

import regex as re

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


def _prune_skipped_steps(
    recipe: Any,
    ingredient_overrides: dict[str, str] | None = None,
) -> tuple[Any, dict[str, bool]]:
    """Evaluate skip_when_false guards and prune steps Python-side.

    Iterates all steps with a skip_when_false field. For each:
    - Truthy value: clears skip_when_false on the step (step becomes mandatory).
    - Falsy value: removes the step and repairs upstream routes.

    Returns a tuple of (pruned_recipe, resolutions) where resolutions maps
    step_name -> bool (True = kept, False = pruned).
    """
    overrides = ingredient_overrides or {}
    resolutions: dict[str, bool] = {}
    working = recipe

    # Collect guarded steps from the original recipe (stable iteration order)
    steps_to_check = [
        name for name, step in recipe.steps.items() if step.skip_when_false is not None
    ]

    for step_name in steps_to_check:
        step = working.steps.get(step_name)
        if step is None or not step.skip_when_false:
            continue
        ref = step.skip_when_false
        if not ref.startswith("inputs."):
            continue
        ingredient_name = ref[len("inputs.") :]

        # Resolve value: explicit override > recipe default > absent (falsy)
        if ingredient_name in overrides:
            value = overrides[ingredient_name]
        else:
            ing = working.ingredients.get(ingredient_name)
            value = (ing.default or "false") if ing is not None else "false"

        is_truthy = value.lower() in ("true", "1", "yes")
        resolutions[step_name] = is_truthy

        if is_truthy:
            new_steps = dict(working.steps)
            new_steps[step_name] = dataclasses.replace(step, skip_when_false=None)
            working = dataclasses.replace(working, steps=new_steps)
        else:
            # Redirect all routes pointing to the pruned step
            redirect = step.on_success
            new_steps = {}
            for name, s in working.steps.items():
                if name == step_name:
                    continue
                fixes: dict[str, Any] = {}
                if s.on_success == step_name:
                    fixes["on_success"] = redirect
                if s.on_failure == step_name:
                    fixes["on_failure"] = redirect
                if s.on_context_limit == step_name:
                    fixes["on_context_limit"] = redirect
                if s.on_exhausted == step_name:
                    fixes["on_exhausted"] = redirect
                new_steps[name] = dataclasses.replace(s, **fixes) if fixes else s
            working = dataclasses.replace(working, steps=new_steps)

    return working, resolutions


def _resolve_skip_guards_in_content(
    raw: str,
    resolutions: dict[str, bool],
    original_steps: dict[str, Any],
) -> str:
    """Apply skip_when_false resolution decisions to the raw YAML content string.

    For each resolved step:
    - Truthy (step kept): strip the skip_when_false line so the step appears mandatory.
    - Falsy (step pruned): replace the ingredient reference with literal "false" so
      the LLM evaluates the literal and skips the step without needing ingredient visibility.
    """
    if not resolutions:
        return raw

    for step_name, is_truthy in resolutions.items():
        step = original_steps.get(step_name)
        if step is None or not step.skip_when_false:
            continue
        ref = step.skip_when_false
        if not ref.startswith("inputs."):
            continue
        ingredient_name = re.escape(ref[len("inputs.") :])
        if is_truthy:
            raw = re.sub(
                rf"(?m)^([ \t]+)skip_when_false:[ \t]+inputs\.{ingredient_name}[ \t]*\n",
                "",
                raw,
            )
        else:
            raw = re.sub(
                rf"(?m)^([ \t]+skip_when_false:[ \t]+)inputs\.{ingredient_name}[ \t]*$",
                r'\1"false"',
                raw,
            )
    return raw
