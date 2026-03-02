"""Recipe validation — structural validation and semantic rules.

Data-flow analysis functions (analyze_dataflow, _build_step_graph, etc.) have been
extracted to recipe/_analysis.py to break the circular import that previously
required rules.py to defer-import these functions inside function bodies.

Rule registration is triggered by recipe/__init__.py importing all rule sub-modules.
This module no longer imports rules.py directly.
"""

from __future__ import annotations

from autoskillit.core import (
    RETRY_RESPONSE_FIELDS,
    get_logger,
)
from autoskillit.recipe._analysis import (
    _build_step_graph,
    analyze_dataflow,
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
from autoskillit.recipe.schema import Recipe

logger = get_logger(__name__)

# Re-export registry symbols so existing ``from autoskillit.recipe.validator import X``
# imports continue to work without modification.
# Also re-export analysis functions for backward compatibility with tests that
# import them from validator.
__all__ = [
    "RuleFinding",
    "RuleSpec",
    "_RULE_REGISTRY",
    "analyze_dataflow",
    "_build_step_graph",
    "build_quality_dict",
    "compute_recipe_validity",
    "filter_version_rule",
    "findings_to_dicts",
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

    for step_name, step in recipe.steps.items():
        discriminators = [d for d in ("tool", "action", "python") if getattr(step, d) is not None]
        if len(discriminators) == 0:
            errors.append(f"Step '{step_name}' must have 'tool', 'action', or 'python'.")
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
        for goto_field in ("on_success", "on_failure", "on_retry"):
            target = getattr(step, goto_field)
            if target and target not in step_names and target != "done":
                errors.append(
                    f"Step '{step_name}'.{goto_field} references unknown step '{target}'."
                )
        if step.on_retry is not None and step.retry is not None and step.retry.on == "needs_retry":
            errors.append(
                f"Step '{step_name}' has both 'on_retry' and 'retry.on=\"needs_retry\"'; "
                f"they are mutually exclusive. Use 'on_retry' to route to a different step "
                f"on needs_retry=True, or 'retry' to re-run the same step — not both."
            )
        if step.retry and step.retry.on_exhausted not in step_names:
            errors.append(
                f"Step '{step_name}'.retry.on_exhausted references "
                f"unknown step '{step.retry.on_exhausted}'."
            )
        if step.retry and step.retry.on and step.retry.on not in RETRY_RESPONSE_FIELDS:
            errors.append(
                f"Step '{step_name}'.retry.on references unknown response field "
                f"'{step.retry.on}'. Valid fields: {sorted(RETRY_RESPONSE_FIELDS)}"
            )
        if step.on_result is not None:
            if step.on_success is not None:
                errors.append(
                    f"Step '{step_name}' has both 'on_result' and 'on_success'; "
                    f"they are mutually exclusive."
                )
            # Predicate format validation
            if step.on_failure is not None:
                errors.append(
                    f"Step '{step_name}' has both 'on_result' (predicate format) and "
                    f"'on_failure'; they are mutually exclusive. Predicate conditions "
                    f"handle all routing paths including failures."
                )
            for i, cond in enumerate(step.on_result.conditions):
                if not cond.route:
                    errors.append(f"Step '{step_name}'.on_result[{i}].route must be non-empty.")
                elif cond.route not in step_names and cond.route != "done":
                    errors.append(
                        f"Step '{step_name}'.on_result[{i}].route references "
                        f"unknown step '{cond.route}'."
                    )

    # Validate capture values: must contain ${{ result.* }} expressions
    for step_name, step in recipe.steps.items():
        for cap_key, cap_val in step.capture.items():
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
    ingredient_names = set(recipe.ingredients.keys())

    for step_name, step, available_context in iter_steps_with_context(recipe):
        for arg_key, arg_val in step.with_args.items():
            if not isinstance(arg_val, str):
                continue
            for ref in _INPUT_REF_RE.findall(arg_val):
                if ref not in ingredient_names:
                    errors.append(
                        f"Step '{step_name}'.with.{arg_key} references undeclared input '{ref}'."
                    )
            for ref in _CONTEXT_REF_RE.findall(arg_val):
                if ref not in available_context:
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
