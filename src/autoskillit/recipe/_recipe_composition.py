"""Sub-recipe composition: merge sub-recipes into parent recipe."""

from __future__ import annotations

import dataclasses
import warnings
from pathlib import Path
from typing import Any

import regex as re

from autoskillit.core import YAMLError, load_yaml
from autoskillit.recipe.io import find_sub_recipe_by_name
from autoskillit.recipe.io import load_recipe as _load_recipe_from_path
from autoskillit.recipe.schema import (
    _TERMINAL_TARGETS,
    Recipe,
    RecipeStep,
    StepResultCondition,
    StepResultRoute,
)  # noqa: F401


def _collect_all_route_targets(step: RecipeStep) -> set[str]:
    """Return all route target names from every routing field on step.

    Mirrors _extract_routing_edges() field enumeration but returns plain strings.
    """
    targets: set[str] = set()
    if step.on_success:
        targets.add(step.on_success)
    if step.on_failure:
        targets.add(step.on_failure)
    if step.on_context_limit:
        targets.add(step.on_context_limit)
    if step.on_exhausted:
        targets.add(step.on_exhausted)
    if step.on_result:
        sr = step.on_result
        if sr.conditions:
            targets.update(c.route for c in sr.conditions)
        elif sr.routes:
            targets.update(sr.routes.values())
    return targets


def _step_block_pattern(escaped_name: str) -> str:
    """Return the regex body matching a 2-space-indented YAML step block (no flags prefix)."""
    return rf"^  {escaped_name}:[ \t]*\n(?:(?:  [ \t][^\n]*|[ \t]*)(?:\n|$))*"


def _strip_step_block(raw: str, step_name: str) -> str:
    """Remove the entire YAML block for step_name from raw YAML content.

    Matches the step header line (2-space indent) and all deeper-indented child lines.
    """
    return re.sub(rf"(?m){_step_block_pattern(re.escape(step_name))}", "", raw)


def _validate_no_dangling_routes(recipe: Recipe) -> list[str]:
    """Return error strings for any route targets that do not exist in recipe.steps."""
    known = frozenset(recipe.steps.keys())
    errors: list[str] = []
    for step_name, step in recipe.steps.items():
        for target in _collect_all_route_targets(step):
            if target not in known and target not in _TERMINAL_TARGETS:
                errors.append(f"Step '{step_name}' routes to unknown step '{target}'")
    return errors


FALSY_STRINGS: frozenset[str] = frozenset({"false", "0", "no", ""})


def _is_ingredient_truthy(value: str) -> bool:
    return bool(value) and value.lower() not in FALSY_STRINGS


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

        if _is_ingredient_truthy(gate_value):
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

        if ref.startswith("inputs."):
            ingredient_name = ref[len("inputs.") :]
            # Resolve value: explicit override > recipe default > absent (falsy)
            if ingredient_name in overrides:
                value = str(overrides[ingredient_name])
            else:
                ing = working.ingredients.get(ingredient_name)
                value = (
                    str(ing.default) if ing is not None and ing.default is not None else "false"
                )
        else:
            # Literal value already resolved — evaluate directly without ingredient lookup
            value = ref

        is_truthy = _is_ingredient_truthy(value)
        resolutions[step_name] = is_truthy

        if is_truthy:
            new_steps = dict(working.steps)
            new_steps[step_name] = dataclasses.replace(step, skip_when_false=None)
            working = dataclasses.replace(working, steps=new_steps)
        else:
            # Redirect all routes pointing to the pruned step; guard against None redirect.
            # For on_result-only steps (on_success is None), derive redirect from the
            # default/else condition (when=None). For legacy routes format, no safe default
            # exists — redirect stays None and _validate_no_dangling_routes catches dangling refs.
            if step.on_success is not None:
                redirect = step.on_success
            elif step.on_result is not None and step.on_result.conditions:
                redirect = next(
                    (c.route for c in step.on_result.conditions if c.when is None), None
                )
            else:
                redirect = None
            new_steps = {}
            for name, s in working.steps.items():
                if name == step_name:
                    continue
                # Fast path: skip steps that do not reference the pruned step at all.
                # _collect_all_route_targets is the single source of truth for routing
                # field enumeration — adding a new routing field there automatically
                # extends this guard's coverage.
                if step_name not in _collect_all_route_targets(s):
                    new_steps[name] = s
                    continue
                fixes: dict[str, Any] = {}
                if s.on_success == step_name and redirect is not None:
                    fixes["on_success"] = redirect
                if s.on_failure == step_name and redirect is not None:
                    fixes["on_failure"] = redirect
                if s.on_context_limit == step_name and redirect is not None:
                    fixes["on_context_limit"] = redirect
                if s.on_exhausted == step_name and redirect is not None:
                    fixes["on_exhausted"] = redirect
                if s.on_result is not None and redirect is not None:
                    sr = s.on_result
                    if sr.conditions:
                        if any(c.route == step_name for c in sr.conditions):
                            fixes["on_result"] = StepResultRoute(
                                conditions=[
                                    StepResultCondition(
                                        when=c.when,
                                        route=redirect if c.route == step_name else c.route,
                                    )
                                    for c in sr.conditions
                                ]
                            )
                    elif sr.routes:
                        if any(v == step_name for v in sr.routes.values()):
                            fixes["on_result"] = StepResultRoute(
                                field=sr.field,
                                routes={
                                    k: (redirect if v == step_name else v)
                                    for k, v in sr.routes.items()
                                },
                            )
                new_steps[name] = dataclasses.replace(s, **fixes) if fixes else s
            recipe_kwargs: dict[str, Any] = {"steps": new_steps}
            if getattr(working, "entry", None) == step_name:
                recipe_kwargs["entry"] = redirect
            working = dataclasses.replace(working, **recipe_kwargs)

    return working, resolutions


def _resolve_skip_guards_in_content(
    raw: str,
    resolutions: dict[str, bool],
    original_steps: dict[str, Any],
) -> str:
    """Apply skip_when_false resolution decisions to the raw YAML content string.

    For each resolved step:
    - Truthy (step kept): strip skip_when_false and optional: true lines so the step
      appears mandatory.
    - Falsy (step pruned): strip the entire step block.
    """
    if not resolutions:
        return raw

    for step_name, is_truthy in resolutions.items():
        step = original_steps.get(step_name)
        if step is None or not step.skip_when_false:
            continue
        ref = step.skip_when_false
        if not is_truthy:
            raw = _strip_step_block(raw, step_name)
            continue
        raw = re.sub(
            rf"(?m)({_step_block_pattern(re.escape(step_name))})",
            lambda m: re.sub(r"(?m)^[ \t]+optional:[ \t]+(?:true|True)[ \t]*\n", "", m.group(0)),
            raw,
        )
        if not ref.startswith("inputs."):
            continue
        ingredient_name = re.escape(ref[len("inputs.") :])
        raw = re.sub(
            rf'(?m)^([ \t]+)skip_when_false:[ \t]+["\']?inputs\.{ingredient_name}["\']?[ \t]*\n',
            "",
            raw,
        )
    return raw


def _assert_content_integrity(
    raw: str,
    resolutions: dict[str, bool],
    original_steps: dict[str, Any],
) -> None:
    """Verify no truthy-resolved step retains optional/skip_when_false signals in content.

    Raises ValueError if optional: true or skip_when_false: inputs.* survive stripping.
    """
    if not resolutions:
        return
    try:
        parsed = load_yaml(raw) or {}
    except YAMLError as exc:
        warnings.warn(
            f"content integrity check skipped — YAMLError parsing resolved content: {exc}",
            RuntimeWarning,
            stacklevel=3,
        )
        return
    parsed_steps: dict[str, Any] = parsed.get("steps", {}) or {}
    for step_name, is_truthy in resolutions.items():
        if not is_truthy:
            continue
        step_data = parsed_steps.get(step_name, {}) or {}
        if step_data.get("optional") is True:
            raise ValueError(
                f"Content integrity violation: step '{step_name}' retains "
                f"'optional: true' after truthy resolution"
            )
        original = original_steps.get(step_name)
        ref = getattr(original, "skip_when_false", None) if original is not None else None
        if ref is not None and ref.startswith("inputs.") and "skip_when_false" in step_data:
            raise ValueError(
                f"Content integrity violation: step '{step_name}' retains "
                f"'skip_when_false' after truthy resolution"
            )
