"""Sub-recipe composition: merge sub-recipes into parent recipe."""

from __future__ import annotations

import dataclasses
import warnings
from pathlib import Path
from typing import Any

import regex as re
from yaml import MappingNode

from autoskillit.core import SKILL_TOOLS, YAMLError, compose_yaml, load_yaml
from autoskillit.recipe._contracts_types import INPUT_REF_RE
from autoskillit.recipe.io import _parse_recipe, find_sub_recipe_by_name
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
    if step.on_rate_limit:
        targets.add(step.on_rate_limit)
    if step.on_exhausted:
        targets.add(step.on_exhausted)
    if step.on_skip:
        targets.add(step.on_skip)
    if step.on_result:
        sr = step.on_result
        if sr.conditions:
            targets.update(c.route for c in sr.conditions)
        elif sr.routes:
            targets.update(sr.routes.values())
    return targets


def _derive_rate_limit_routes(recipe: Recipe) -> Recipe:
    """Auto-populate on_rate_limit from on_context_limit for run_skill steps.

    When a run_skill step declares on_context_limit but not on_rate_limit,
    the sous-chef silently borrows on_context_limit for transient 429 failures.
    This derivation makes that behavior explicit at the schema level, removing
    the silent fallback and making the routing visible to semantic rules.

    Returns a recipe with copied steps. Steps with explicit on_rate_limit are
    left untouched (explicit overrides win). Steps with neither field set are
    also untouched (they remain flagged by the run-skill-missing-rate-limit
    semantic rule).
    """
    steps = dict(recipe.steps)
    for name, step in recipe.steps.items():
        if step.tool not in SKILL_TOOLS:
            continue
        if step.on_rate_limit is not None:
            continue
        if step.on_context_limit is not None:
            steps[name] = dataclasses.replace(step, on_rate_limit=step.on_context_limit)
    return dataclasses.replace(recipe, steps=steps)


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


def _declared_route_signatures(recipe: Recipe) -> tuple[tuple[Any, ...], ...]:
    signatures: list[tuple[Any, ...]] = []
    scalar_fields = (
        "on_success",
        "on_failure",
        "on_context_limit",
        "on_rate_limit",
        "on_exhausted",
        "on_skip",
    )
    for step_name, step in recipe.steps.items():
        for field in scalar_fields:
            target = getattr(step, field)
            if target is not None:
                signatures.append((step_name, field, target))
        if step.on_result is None:
            continue
        if step.on_result.conditions:
            signatures.extend(
                (step_name, "on_result", index, condition.when, condition.route)
                for index, condition in enumerate(step.on_result.conditions)
            )
        else:
            signatures.extend(
                (step_name, "on_result", step.on_result.field, key, target)
                for key, target in step.on_result.routes.items()
            )
    return tuple(signatures)


def _validate_route_consistency(raw: str, recipe: Recipe) -> list[str]:
    """Compare ordered steps and exact declared routes in raw and source models."""
    try:
        data = load_yaml(raw)
        raw_recipe = _parse_recipe(data)
    except (TypeError, ValueError, YAMLError) as exc:
        return [f"Repaired raw YAML cannot be parsed: {exc}"]
    errors: list[str] = []
    step_order_matches = tuple(raw_recipe.steps) == tuple(recipe.steps)
    if not step_order_matches:
        errors.append(
            "Raw YAML step order differs from the Python model: "
            f"{tuple(raw_recipe.steps)!r} != {tuple(recipe.steps)!r}"
        )
    if step_order_matches:
        raw_signatures = _declared_route_signatures(raw_recipe)
        model_signatures = _declared_route_signatures(recipe)
        if raw_signatures != model_signatures:
            errors.append(
                "Raw YAML declared routes differ from the Python model: "
                f"{raw_signatures!r} != {model_signatures!r}"
            )
    return errors


FALSY_STRINGS: frozenset[str] = frozenset({"false", "0", "no", ""})


def _is_ingredient_truthy(value: str) -> bool:
    return bool(value) and value.lower() not in FALSY_STRINGS


def _rewrite_step_routes(step: RecipeStep, redirects: dict[str, str]) -> RecipeStep:
    def rewrite(target: str | None) -> str | None:
        return redirects.get(target, target) if target is not None else None

    result_route = step.on_result
    if result_route is not None:
        if result_route.conditions:
            result_route = StepResultRoute(
                conditions=[
                    StepResultCondition(when=condition.when, route=rewrite(condition.route) or "")
                    for condition in result_route.conditions
                ]
            )
        else:
            result_route = StepResultRoute(
                field=result_route.field,
                routes={
                    key: rewrite(target) or target for key, target in result_route.routes.items()
                },
            )
    return dataclasses.replace(
        step,
        on_success=rewrite(step.on_success),
        on_failure=rewrite(step.on_failure),
        on_context_limit=rewrite(step.on_context_limit),
        on_rate_limit=rewrite(step.on_rate_limit),
        on_exhausted=rewrite(step.on_exhausted) or step.on_exhausted,
        on_skip=rewrite(step.on_skip),
        on_result=result_route,
    )


def _resolve_skip_redirects(
    steps: dict[str, RecipeStep], resolutions: dict[str, bool | None]
) -> dict[str, str]:
    redirects: dict[str, str] = {}
    falsy = {name for name, resolution in resolutions.items() if resolution is False}
    for start in falsy:
        current = start
        visited: set[str] = set()
        while current in falsy:
            if current in visited:
                raise ValueError(f"on_skip cycle encountered while resolving '{start}'")
            visited.add(current)
            step = steps.get(current)
            if step is None or step.on_skip is None:
                raise ValueError(f"Skipped step '{current}' has no valid on_skip target")
            current = step.on_skip
        if current not in steps:
            raise ValueError(f"Skipped step '{start}' resolves to unknown step '{current}'")
        redirects[start] = current
    return redirects


def _move_step_to_front(steps: dict[str, RecipeStep], step_name: str) -> dict[str, RecipeStep]:
    return {
        step_name: steps[step_name],
        **{name: step for name, step in steps.items() if name != step_name},
    }


def _drop_sub_recipe_step(recipe: Recipe, step_name: str) -> Recipe:
    """Drop a false-gated placeholder and preserve its attachment point."""
    placeholder = recipe.steps[step_name]
    continuation = placeholder.on_success
    is_entry = next(iter(recipe.steps), None) == step_name
    if (
        continuation is None
        or continuation == step_name
        or (
            continuation not in recipe.steps
            and (continuation not in _TERMINAL_TARGETS or is_entry)
        )
    ):
        raise ValueError(
            f"Sub-recipe placeholder '{step_name}' has no surviving on_success continuation"
        )
    new_steps = {
        name: _rewrite_step_routes(step, {step_name: continuation})
        for name, step in recipe.steps.items()
        if name != step_name
    }
    if is_entry:
        new_steps = _move_step_to_front(new_steps, continuation)
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
    defer_unresolved: bool = False,
) -> tuple[Any, dict[str, bool | None]]:
    """Evaluate skip_when_false guards and prune steps Python-side.

    Iterates all steps with a skip_when_false field. For each:
    - Truthy value: clears skip_when_false on the step (step becomes mandatory).
    - Falsy value: removes the step and repairs upstream routes.
    - None (deferred): when defer_unresolved=True and the ingredient is absent from
      overrides, the step is kept with skip_when_false cleared and resolution None.

    Returns a tuple of (pruned_recipe, resolutions) where resolutions maps
    step_name -> bool | None (True = kept, False = pruned, None = deferred).
    """
    overrides = ingredient_overrides or {}
    resolutions: dict[str, bool | None] = {}
    for step_name, step in recipe.steps.items():
        ref = step.skip_when_false
        if ref is None:
            continue
        if ref.startswith("inputs."):
            ingredient_name = ref[len("inputs.") :]
            if ingredient_name in overrides:
                value = str(overrides[ingredient_name])
            elif defer_unresolved:
                resolutions[step_name] = None
                continue
            else:
                ingredient = recipe.ingredients.get(ingredient_name)
                value = (
                    str(ingredient.default)
                    if ingredient is not None and ingredient.default is not None
                    else "false"
                )
        else:
            value = ref
        resolutions[step_name] = _is_ingredient_truthy(value)

    redirects = _resolve_skip_redirects(recipe.steps, resolutions)
    steps: dict[str, RecipeStep] = {}
    for name, step in recipe.steps.items():
        resolution = resolutions.get(name, True)
        if resolution is False:
            continue
        if name in resolutions:
            step = dataclasses.replace(
                step,
                skip_when_false=None,
                on_skip=None,
                optional=False if resolution is True else step.optional,
            )
        steps[name] = _rewrite_step_routes(step, redirects)
    working = dataclasses.replace(recipe, steps=steps)
    first = next(iter(recipe.steps), None)
    if first in redirects:
        entry = redirects[first]
        working = dataclasses.replace(
            working,
            steps=_move_step_to_front(working.steps, entry),
        )
    return working, resolutions


def _resolve_skip_guards_in_content(
    raw: str,
    resolutions: dict[str, bool | None],
    original_steps: dict[str, RecipeStep],
) -> str:
    """Apply skip_when_false resolution decisions to the raw YAML content string.

    For each resolved step:
    - Truthy (step kept): strip skip_when_false and optional: true lines so the step
      appears mandatory.
    - Falsy (step pruned): strip the entire step block.
    """
    if not resolutions:
        return raw
    root = compose_yaml(raw)
    if not isinstance(root, MappingNode):
        raise ValueError("Guarded recipe must be a YAML mapping")
    steps_node = None
    for key_node, value_node in root.value:
        if getattr(key_node, "value", None) == "steps":
            steps_node = value_node
            break
    if not isinstance(steps_node, MappingNode):
        raise ValueError("Guarded recipe requires a block-style top-level steps mapping")
    if getattr(steps_node, "flow_style", False):
        raise ValueError("Guarded recipe does not support a flow-style top-level steps mapping")

    counts: dict[int, int] = {}
    expanded: set[int] = set()

    def count_nodes(node: Any) -> None:
        identity = id(node)
        counts[identity] = counts.get(identity, 0) + 1
        if identity in expanded:
            return
        expanded.add(identity)
        value = getattr(node, "value", None)
        if isinstance(value, list):
            for item in value:
                if isinstance(item, tuple):
                    count_nodes(item[0])
                    count_nodes(item[1])
                else:
                    count_nodes(item)

    count_nodes(root)

    def descendants(node: Any, visited: set[int]) -> list[Any]:
        identity = id(node)
        if identity in visited:
            return []
        visited.add(identity)
        found = [node]
        value = getattr(node, "value", None)
        if isinstance(value, list):
            for item in value:
                if isinstance(item, tuple):
                    found.extend(descendants(item[0], visited))
                    found.extend(descendants(item[1], visited))
                else:
                    found.extend(descendants(item, visited))
        return found

    if any(counts.get(id(node), 0) > 1 for node in descendants(steps_node, set())):
        raise ValueError("Guarded recipe does not support aliases within steps")

    def line_start(index: int) -> int:
        return raw.rfind("\n", 0, index) + 1

    def line_end(index: int) -> int:
        newline = raw.find("\n", index)
        return len(raw) if newline < 0 else newline + 1

    redirects = _resolve_skip_redirects(original_steps, resolutions)
    entries = list(steps_node.value)
    mapping_end = (
        len(raw)
        if steps_node.end_mark.index >= len(raw)
        else line_start(steps_node.end_mark.index)
    )
    blocks: dict[str, str] = {}
    order: list[str] = []
    route_fields = {
        "on_success",
        "on_failure",
        "on_context_limit",
        "on_rate_limit",
        "on_exhausted",
        "route",
    }
    for index, (name_node, step_node) in enumerate(entries):
        name = str(name_node.value)
        order.append(name)
        start = line_start(name_node.start_mark.index)
        end = (
            line_start(entries[index + 1][0].start_mark.index)
            if index + 1 < len(entries)
            else mapping_end
        )
        if resolutions.get(name) is False:
            continue
        edits: list[tuple[int, int, str]] = []
        for key_node, value_node in step_node.value:
            key = str(key_node.value)
            if key in {"skip_when_false", "on_skip"} or (
                key == "optional" and resolutions.get(name) is True
            ):
                edits.append(
                    (
                        line_start(key_node.start_mark.index),
                        line_end(value_node.end_mark.index),
                        "",
                    )
                )

        def collect_route_edits(node: Any, parent_key: str | None = None) -> None:
            value = getattr(node, "value", None)
            if not isinstance(value, list):
                return
            for item in value:
                if isinstance(item, tuple):
                    key_node, value_node = item
                    key = str(getattr(key_node, "value", ""))
                    scalar = getattr(value_node, "value", None)
                    is_legacy_route = parent_key == "routes"
                    if isinstance(scalar, str) and (key in route_fields or is_legacy_route):
                        replacement = redirects.get(scalar)
                        if replacement is not None:
                            style = getattr(value_node, "style", None)
                            rendered = replacement
                            if style == "'":
                                rendered = "'" + replacement.replace("'", "''") + "'"
                            elif style == '"':
                                rendered = (
                                    '"'
                                    + replacement.replace("\\", "\\\\").replace('"', '\\"')
                                    + '"'
                                )
                            edits.append(
                                (value_node.start_mark.index, value_node.end_mark.index, rendered)
                            )
                    collect_route_edits(value_node, key)
                else:
                    collect_route_edits(item, parent_key)

        collect_route_edits(step_node)
        ordered_edits = sorted(edits)
        for previous, current in zip(ordered_edits, ordered_edits[1:]):
            if previous[1] > current[0]:
                raise ValueError(f"Overlapping YAML edit spans in step '{name}'")
        block = raw[start:end]
        for edit_start, edit_end, replacement in sorted(edits, reverse=True):
            block = block[: edit_start - start] + replacement + block[edit_end - start :]
        blocks[name] = block

    surviving = [name for name in order if name in blocks]
    if order and order[0] in redirects:
        entry = redirects[order[0]]
        surviving = [entry, *[name for name in surviving if name != entry]]
    content_start = line_start(entries[0][0].start_mark.index)
    content_end = mapping_end
    return raw[:content_start] + "".join(blocks[name] for name in surviving) + raw[content_end:]


_MODEL_COND_RE = re.compile(
    r"""\$\{\{\s*'([^']+)'\s+if\s+inputs\.(\w+)\s*==\s*'([^']+)'\s+else\s+'([^']+)'\s*\}\}"""
)


def _resolve_hidden_inputs_in_content(
    raw: str,
    recipe: Any,
    ingredient_overrides: dict[str, str] | None,
) -> str:
    """Substitute hidden ingredient ${{ inputs.<name> }} templates in raw YAML content.

    Only hidden ingredients are resolved — visible ingredient refs remain as literals
    for the LLM to substitute using the ingredients table values.
    """

    overrides = ingredient_overrides or {}
    hidden_ingredients = {
        name: ing
        for name, ing in (recipe.ingredients or {}).items()
        if getattr(ing, "hidden", False)
    }
    if not hidden_ingredients:
        return raw

    # First pass: resolve conditional expressions in model: fields
    # Pattern: ${{ 'val_a' if inputs.name == 'cond' else 'val_b' }}
    def _resolve_model_cond(m: re.Match[str]) -> str:
        val_a, name, cond, val_b = m.group(1), m.group(2), m.group(3), m.group(4)
        if name not in hidden_ingredients:
            return m.group(0)
        value = overrides.get(name)
        if value is None:
            ing = hidden_ingredients[name]
            default = getattr(ing, "default", None)
            value = str(default) if default is not None else ""
        return val_a if value == cond else val_b

    raw = _MODEL_COND_RE.sub(_resolve_model_cond, raw)

    # Second pass: resolve simple ${{ inputs.name }} references for hidden ingredients
    def _resolve_ref(m: re.Match[str]) -> str:
        name = m.group(1)
        if name not in hidden_ingredients:
            return m.group(0)
        value = overrides.get(name)
        if value is None:
            ing = hidden_ingredients[name]
            default = getattr(ing, "default", None)
            value = str(default) if default is not None else ""
        return value

    return INPUT_REF_RE.sub(_resolve_ref, raw)


def _assert_content_integrity(
    raw: str,
    resolutions: dict[str, bool | None],
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
        if is_truthy is False:
            continue
        step_data = parsed_steps.get(step_name, {}) or {}
        if is_truthy is True and step_data.get("optional") is True:
            raise ValueError(
                f"Content integrity violation: step '{step_name}' retains "
                f"'optional: true' after truthy resolution"
            )
        original = original_steps.get(step_name)
        ref = getattr(original, "skip_when_false", None) if original is not None else None
        if ref is not None and ref.startswith("inputs.") and "skip_when_false" in step_data:
            raise ValueError(
                f"Content integrity violation: step '{step_name}' retains "
                f"'skip_when_false' after guard resolution"
            )
        if "on_skip" in step_data:
            raise ValueError(
                f"Content integrity violation: step '{step_name}' retains "
                "'on_skip' after guard resolution"
            )
