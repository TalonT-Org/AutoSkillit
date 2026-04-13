"""Recipe validation — structural checks and registry re-exports.

Data-flow analysis functions have been extracted to ``_analysis.py``
to break the circular import between validator.py and the rule modules.
"""

from __future__ import annotations

from autoskillit.core import (
    get_logger,
)
from autoskillit.recipe._analysis import (  # noqa: F401
    ValidationContext,
    _build_step_graph,
    analyze_dataflow,
    make_validation_context,
)
from autoskillit.recipe.contracts import (
    _CONTEXT_REF_RE,
    _INPUT_REF_RE,
    _TEMPLATE_REF_RE,
)
from autoskillit.recipe.io import iter_steps_with_context
from autoskillit.recipe.registry import (
    _RULE_REGISTRY,
    RuleFinding,
    RuleSpec,
    build_quality_dict,
    compute_recipe_validity,
    filter_version_rule,
    findings_to_dicts,
    run_semantic_rules,
    semantic_rule,
)
from autoskillit.recipe.schema import _TERMINAL_TARGETS, Recipe

logger = get_logger(__name__)

# Re-export registry symbols here so the public interface stays in validator.py.
# The registry was extracted to registry.py to break the circular import between
# validator.py and the rule modules (rules_*.py all import from validator via the
# registry). Callers import from validator.py as the single public entry point.
__all__ = [
    "RuleFinding",
    "RuleSpec",
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


def validate_recipe(recipe: Recipe) -> list[str]:
    """Return a list of validation errors (empty if valid)."""
    errors: list[str] = []

    if not recipe.name:
        errors.append("Recipe must have a 'name'.")
    if not recipe.steps:
        errors.append("Recipe must have at least one step.")

    step_names = set(recipe.steps.keys())

    ingredient_names = set(recipe.ingredients.keys())

    for step_name, step in recipe.steps.items():
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
        for goto_field in ("on_success", "on_failure", "on_context_limit"):
            target = getattr(step, goto_field)
            if target and target not in step_names and target != "done":
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
            all_refs = _TEMPLATE_REF_RE.findall(cap_val)
            if not all_refs:
                errors.append(
                    f"Step '{step_name}'.capture.{cap_key} must contain "
                    f"a ${{{{ result.* }}}} expression."
                )
            for ref_match in all_refs:
                inner = ref_match[3:-2].strip()
                if not inner.startswith("result."):
                    errors.append(
                        f"Step '{step_name}'.capture.{cap_key} references "
                        f"'{inner}'; capture values must use the 'result.' namespace."
                    )

    # Validate input and context references in with_args using iter_steps_with_context
    # sub_recipe steps have no with_args to validate — skip them.
    for step_name, step, available_context in iter_steps_with_context(recipe):
        if step.sub_recipe is not None:
            continue
        for arg_key, arg_val in step.with_args.items():
            if not isinstance(arg_val, str):
                continue
            for ref in _INPUT_REF_RE.findall(arg_val):
                if ref not in ingredient_names:
                    errors.append(
                        f"Step '{step_name}'.with.{arg_key} references undeclared input '{ref}'."
                    )
            for ref in _CONTEXT_REF_RE.findall(arg_val):
                if ref not in available_context and ref not in step.optional_context_refs:
                    errors.append(
                        f"Step '{step_name}'.with.{arg_key} references "
                        f"context variable '{ref}' which has not been "
                        f"captured by a preceding step."
                    )

    if not recipe.kitchen_rules:
        errors.append(
            "Recipe has no 'kitchen_rules' field. Recipes should include "
            "orchestrator discipline constraints."
        )

    return errors


# Re-export test-access symbols from their new locations.
from autoskillit.recipe.rules_inputs import _check_outdated_version  # noqa: E402 F401
from autoskillit.recipe.rules_worktree import _WORKTREE_CREATING_SKILLS  # noqa: E402 F401
