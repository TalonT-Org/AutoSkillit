"""Recipe validation — structural checks and registry re-exports.

Data-flow analysis functions have been extracted to ``_analysis.py``
to break the circular import between validator.py and the rule modules.
"""

from __future__ import annotations

from autoskillit.core import (
    FinalizedRecipeSegment,
    RecipeFlowEdge,
    get_logger,
    get_tool_def,
)
from autoskillit.recipe._analysis import (  # noqa: F401
    ValidationContext,
    _build_step_graph,
    _extract_routing_edges,
    analyze_dataflow,
    make_validation_context,
)
from autoskillit.recipe.contracts import (
    _CONTEXT_REF_RE,
    _TEMPLATE_REF_RE,
    INPUT_REF_RE,
)
from autoskillit.recipe.io import iter_steps_with_context
from autoskillit.recipe.registry import (
    _RULE_REGISTRY,
    RuleDef,
    RuleFinding,
    build_quality_dict,
    compute_recipe_validity,
    filter_version_rule,
    findings_to_dicts,
    run_semantic_rules,
    semantic_rule,
)
from autoskillit.recipe.schema import _TERMINAL_TARGETS, Recipe, RecipeKind

logger = get_logger(__name__)

# Re-export registry symbols here so the public interface stays in validator.py.
# The registry was extracted to registry.py to break the circular import between
# validator.py and the rule modules (rules_*.py all import from validator via the
# registry). Callers import from validator.py as the single public entry point.
__all__ = [
    "RuleFinding",
    "RuleDef",
    "ValidationContext",
    "_RULE_REGISTRY",
    "_build_step_graph",
    "analyze_dataflow",
    "build_quality_dict",
    "compute_recipe_validity",
    "filter_version_rule",
    "findings_to_dicts",
    "make_validation_context",
    "run_semantic_rules",
    "semantic_rule",
]

# ---------------------------------------------------------------------------
# Structural validation
# ---------------------------------------------------------------------------

_SKILL_HINT = " (Use /autoskillit:write-recipe for schema guidance)"


def _iter_string_leaves(value: object, path: str) -> list[tuple[str, str]]:
    if isinstance(value, str):
        return [(path, value)]
    if isinstance(value, dict):
        leaves: list[tuple[str, str]] = []
        for key, nested in value.items():
            leaves.extend(_iter_string_leaves(nested, f"{path}.{key}"))
        return leaves
    if isinstance(value, (list, tuple)):
        leaves = []
        for index, nested in enumerate(value):
            leaves.extend(_iter_string_leaves(nested, f"{path}[{index}]"))
        return leaves
    return []


def validate_recipe_structure(recipe: Recipe) -> list[str]:
    """Return structural validation errors (empty if valid).

    Does not run semantic rules or contract checks; use validate_from_path()
    for complete validation.
    """
    errors: list[str] = []

    if not recipe.name:
        errors.append("Recipe must have a 'name'." + _SKILL_HINT)

    if recipe.kind == RecipeKind.CAMPAIGN:
        if not recipe.dispatches:
            errors.append("Campaign recipe must have at least one dispatch.")
        return errors

    if not recipe.steps:
        errors.append("Recipe must have at least one step." + _SKILL_HINT)

    step_names = set(recipe.steps.keys())

    if recipe.delivery_segments:
        segment_names = [segment.name.strip() for segment in recipe.delivery_segments]
        if any(not name for name in segment_names):
            errors.append("Delivery segment names must be non-empty.")
        if len(segment_names) != len(set(segment_names)):
            errors.append("Delivery segment names must be unique.")
        declared_steps = [
            step_name for segment in recipe.delivery_segments for step_name in segment.steps
        ]
        if any(not segment.steps for segment in recipe.delivery_segments):
            errors.append("Delivery segments must each contain at least one step.")
        unknown_steps = sorted(set(declared_steps) - step_names)
        if unknown_steps:
            errors.append(f"Delivery segments reference unknown steps: {unknown_steps!r}.")
        duplicates = sorted(
            step_name for step_name in set(declared_steps) if declared_steps.count(step_name) > 1
        )
        if duplicates:
            errors.append(f"Delivery segments contain duplicate steps: {duplicates!r}.")
        ordered_steps = list(recipe.steps)
        if not unknown_steps and not duplicates and declared_steps != ordered_steps:
            errors.append(
                "Delivery segments must contain every recipe step exactly once in "
                "declaration order."
            )

    ingredient_names = set(recipe.ingredients.keys())

    for step_name, step in recipe.steps.items():
        if step.skip_when_false is not None and step.on_skip is None:
            errors.append(
                f"Step '{step_name}' has 'skip_when_false' but is missing required 'on_skip'."
            )
        if step.skip_when_false is None and step.on_skip is not None:
            errors.append(f"Step '{step_name}' has 'on_skip' without 'skip_when_false'.")
        if step.on_skip is not None and step.on_skip not in step_names:
            errors.append(f"Step '{step_name}'.on_skip references unknown step '{step.on_skip}'.")

        if step.sub_recipe is not None:
            other_discriminators = [
                d for d in ("tool", "action", "python", "constant") if getattr(step, d) is not None
            ]
            if other_discriminators:
                errors.append(
                    f"Step '{step_name}' has both 'sub_recipe' and "
                    f"({', '.join(other_discriminators)}); sub_recipe is mutually exclusive."
                )
            if not step.gate:
                errors.append(
                    f"Step '{step_name}' (sub_recipe: '{step.sub_recipe}')"
                    " must have a 'gate' field."
                )
            elif step.gate not in ingredient_names:
                errors.append(
                    f"Step '{step_name}'.gate references undeclared ingredient '{step.gate}'."
                )
            if not step.on_success:
                errors.append(
                    f"Step '{step_name}' (sub_recipe: '{step.sub_recipe}') must have 'on_success'."
                )
            # sub_recipe steps skip discriminator/with_args/capture/on_result validation below
            continue

        discriminators = [
            d for d in ("tool", "action", "python", "constant") if getattr(step, d) is not None
        ]
        if len(discriminators) == 0:
            errors.append(
                f"Step '{step_name}' must have 'tool', 'action', 'python', or 'constant'."
            )
        if len(discriminators) > 1:
            errors.append(
                f"Step '{step_name}' has multiple discriminators "
                f"({', '.join(discriminators)}); pick one."
            )
        if step.python is not None and "." not in step.python:
            errors.append(
                f"Step '{step_name}'.python must be a dotted path "
                f"(module.function), got '{step.python}'."
            )
        if step.action == "stop" and not step.message:
            errors.append(f"Terminal step '{step_name}' (action: stop) must have a 'message'.")
        if step.action == "confirm":
            if not step.message:
                errors.append(
                    f"Confirm step '{step_name}' (action: confirm) must have a 'message'."
                )
            if not step.on_success:
                errors.append(
                    f"Confirm step '{step_name}' (action: confirm) must have 'on_success'."
                )
            if not step.on_failure:
                errors.append(
                    f"Confirm step '{step_name}' (action: confirm) must have 'on_failure'."
                )

        # Routing target validation
        for goto_field in ("on_success", "on_failure", "on_context_limit", "on_rate_limit"):
            target = getattr(step, goto_field)
            if target and target not in step_names and target not in _TERMINAL_TARGETS:
                errors.append(
                    f"Step '{step_name}'.{goto_field} references unknown step '{target}'."
                )

        # on_exhausted: may be a step name OR one of the reserved terminal targets
        if step.on_exhausted not in step_names and step.on_exhausted not in _TERMINAL_TARGETS:
            errors.append(
                f"Step '{step_name}'.on_exhausted references unknown step '{step.on_exhausted}'."
            )

        # retries must be a non-negative integer
        if not isinstance(step.retries, int) or step.retries < 0:
            errors.append(
                f"Step '{step_name}'.retries must be a non-negative integer, got {step.retries!r}."
            )

        if step.stale_threshold is not None and (
            not isinstance(step.stale_threshold, int) or step.stale_threshold <= 0
        ):
            errors.append(
                f"Step {step_name!r}: 'stale_threshold' must be a positive integer "
                f"when set, got {step.stale_threshold!r}"
            )

        if step.idle_output_timeout is not None and (
            not isinstance(step.idle_output_timeout, int) or step.idle_output_timeout < 0
        ):
            errors.append(
                f"Step {step_name!r}: 'idle_output_timeout' must be a non-negative integer "
                f"when set (0 = disabled), got {step.idle_output_timeout!r}"
            )

        if step.on_result is not None:
            if step.on_success is not None:
                errors.append(
                    f"Step '{step_name}' has both 'on_result' and 'on_success'; "
                    f"they are mutually exclusive."
                )
            if step.on_result.conditions:
                # Predicate format validation
                for i, cond in enumerate(step.on_result.conditions):
                    if not cond.route:
                        errors.append(
                            f"Step '{step_name}'.on_result[{i}].route must be non-empty."
                        )
                    elif cond.route not in step_names and cond.route != "done":
                        errors.append(
                            f"Step '{step_name}'.on_result[{i}].route references "
                            f"unknown step '{cond.route}'."
                        )
            else:
                # Legacy format validation
                if not step.on_result.field:
                    errors.append(f"Step '{step_name}'.on_result.field must be non-empty.")
                if not step.on_result.routes:
                    errors.append(f"Step '{step_name}'.on_result.routes must be non-empty.")
                for value, target in step.on_result.routes.items():
                    if target not in step_names and target != "done":
                        errors.append(
                            f"Step '{step_name}'.on_result.routes.{value} references "
                            f"unknown step '{target}'."
                        )

    # Validate capture values: must contain ${{ result.* }} expressions
    # (constant steps use literal capture values — no template expression needed)
    # sub_recipe steps are placeholders — skip capture validation for them.
    for step_name, step in recipe.steps.items():
        if step.sub_recipe is not None:
            continue
        for cap_key, cap_val in step.capture.items():
            if step.constant is not None:
                continue
            all_refs = _TEMPLATE_REF_RE.findall(cap_val.from_)
            if not all_refs:
                errors.append(
                    f"Step '{step_name}'.capture.{cap_key} must contain "
                    f"a ${{{{ result.* }}}} expression." + _SKILL_HINT
                )
            for ref_match in all_refs:
                inner = ref_match[3:-2].strip()
                if not inner.startswith("result."):
                    errors.append(
                        f"Step '{step_name}'.capture.{cap_key} references "
                        f"'{inner}'; capture values must use the 'result.' namespace."
                        + _SKILL_HINT
                    )

    checked_skip_steps: set[str] = set()
    for step_name in recipe.steps:
        if step_name in checked_skip_steps:
            continue
        path: list[str] = []
        path_index: dict[str, int] = {}
        current = step_name
        while current in recipe.steps and (target := recipe.steps[current].on_skip) is not None:
            if current in path_index:
                cycle = path[path_index[current] :] + [current]
                errors.append(f"on_skip cycle detected: {' -> '.join(cycle)}.")
                break
            if current in checked_skip_steps:
                break
            path_index[current] = len(path)
            path.append(current)
            current = target
        checked_skip_steps.update(path)

    # Validate input and context references in with_args using iter_steps_with_context
    # sub_recipe steps have no with_args to validate — skip them.
    for step_name, step, available_context in iter_steps_with_context(recipe):
        if step.sub_recipe is not None:
            continue
        for arg_key, arg_val in step.with_args.items():
            for arg_path, string_value in _iter_string_leaves(arg_val, arg_key):
                for ref in INPUT_REF_RE.findall(string_value):
                    if ref not in ingredient_names:
                        errors.append(
                            f"Step '{step_name}'.with.{arg_path} references undeclared "
                            f"input '{ref}'." + _SKILL_HINT
                        )
                for ref in _CONTEXT_REF_RE.findall(string_value):
                    if ref not in available_context and ref not in step.optional_context_refs:
                        errors.append(
                            f"Step '{step_name}'.with.{arg_path} references "
                            f"context variable '{ref}' which has not been "
                            f"captured by a preceding step."
                        )

    if not recipe.kitchen_rules:
        errors.append(
            "Recipe has no 'kitchen_rules' field. Recipes should include "
            "orchestrator discipline constraints." + _SKILL_HINT
        )

    return errors


def _finalize_delivery_segments(
    recipe: Recipe,
    ordered_flow_edges: tuple[RecipeFlowEdge, ...],
) -> tuple[tuple[FinalizedRecipeSegment, ...], list[str]]:
    """Normalize declared segments against one post-prune recipe graph."""
    if not recipe.delivery_segments:
        return (), []

    surviving_steps = set(recipe.steps)
    normalized = [
        (segment.name.strip(), tuple(step for step in segment.steps if step in surviving_steps))
        for segment in recipe.delivery_segments
    ]
    normalized = [(name, steps) for name, steps in normalized if steps]
    flattened = tuple(step for _name, steps in normalized for step in steps)
    ordered_steps = tuple(recipe.steps)
    errors: list[str] = []
    if flattened != ordered_steps:
        errors.append(
            "Finalized delivery segments must partition post-prune steps in declaration order."
        )
        return (), errors
    if not normalized or ordered_steps[0] not in normalized[0][1]:
        errors.append("The initial delivery segment must contain the finalized entrypoint.")
        return (), errors

    segment_index = {
        step_name: index for index, (_name, steps) in enumerate(normalized) for step_name in steps
    }
    projected_targets = {
        step_name: {edge.target for edge in ordered_flow_edges if edge.source == step_name}
        for step_name, step in recipe.steps.items()
        if step.action == "route"
    }
    for step_name, targets in projected_targets.items():
        route_source_index = segment_index[step_name]
        later_targets = {
            edge.target
            for edge in _extract_routing_edges(recipe.steps[step_name])
            if edge.target in segment_index and segment_index[edge.target] > route_source_index
        }
        missing_targets = sorted(later_targets - targets)
        if missing_targets:
            errors.append(
                f"Route action step {step_name!r} has later-segment targets missing from "
                f"its finalized pull closure: {missing_targets!r}."
            )

    checkpoint_sources: list[list[str]] = [[] for _ in normalized]
    for edge in ordered_flow_edges:
        source_index = segment_index.get(edge.source)
        target_index = segment_index.get(edge.target)
        if source_index is None or target_index is None or source_index >= target_index:
            continue
        step = recipe.steps[edge.source]
        if step.tool is None:
            if step.action != "route":
                errors.append(
                    f"Cross-segment route from step {edge.source!r} requires a tool carrier "
                    "or an action: route pull closure."
                )
                continue
        else:
            tool_name = "complete_run_skill_result" if step.tool == "run_skill" else step.tool
            definition = get_tool_def(tool_name)
            if definition is None:
                errors.append(
                    f"Delivery checkpoint step {edge.source!r} uses unregistered tool "
                    f"{step.tool!r}."
                )
                continue
            success_crossing = edge_routes_success(
                tool_name,
                edge,
                automatic=definition.automatic_recipe_delivery,
                recovery=definition.recovery_recipe_delivery,
            )
            capability = (
                definition.automatic_recipe_delivery
                if success_crossing
                else definition.recovery_recipe_delivery
            )
            if not capability:
                required = (
                    "automatic_recipe_delivery" if success_crossing else "recovery_recipe_delivery"
                )
                errors.append(
                    f"Delivery checkpoint step {edge.source!r} tool {step.tool!r} lacks "
                    f"{required} for its {edge.edge_type!r} cross-segment route."
                )
                continue
        if step.tool is not None and step.with_args.get("step_name") != edge.source:
            errors.append(
                f"Delivery checkpoint step {edge.source!r} must pass its exact recipe key "
                "as with.step_name."
            )
            continue
        if edge.source not in checkpoint_sources[target_index]:
            checkpoint_sources[target_index].append(edge.source)

    if errors:
        return (), errors
    return (
        tuple(
            FinalizedRecipeSegment(
                name=name,
                ordered_step_names=steps,
                checkpoint_sources=tuple(checkpoint_sources[index]),
            )
            for index, (name, steps) in enumerate(normalized)
        ),
        [],
    )


def edge_routes_success(
    tool_name: str,
    edge: RecipeFlowEdge,
    *,
    automatic: bool,
    recovery: bool,
) -> bool:
    """Return whether one finalized tool edge selects the success carrier."""
    if not automatic and recovery:
        return False
    if edge.edge_type == "success":
        return True
    if edge.edge_type != "result_condition":
        return False
    condition = (edge.condition or "").replace("'", "")
    if tool_name == "wait_for_ci":
        return "result.conclusion" in condition and "== success" in condition
    if tool_name == "wait_for_merge_queue":
        return "result.pr_state" in condition and "== merged" in condition
    if tool_name == "claim_and_resolve_issue":
        return "result.claimed" in condition and "== true" in condition
    return automatic


# Re-export test-access symbols from their new locations.
from autoskillit.recipe.rules.rules_inputs import _check_outdated_version  # noqa: E402 F401
from autoskillit.recipe.rules.rules_worktree import _WORKTREE_MODIFYING_SKILLS  # noqa: E402 F401
