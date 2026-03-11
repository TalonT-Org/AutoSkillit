"""Semantic rules for run_recipe step composition contracts."""

from __future__ import annotations

import re

from autoskillit.core import Severity
from autoskillit.recipe._analysis import ValidationContext
from autoskillit.recipe.registry import RuleFinding, semantic_rule

_TEMPLATE_REF_RE = re.compile(r"\$\{\{.*?\}\}")


@semantic_rule(
    name="unknown-sub-recipe",
    description=(
        "run_recipe step's 'name' argument must reference a known recipe. "
        "Template references (${{ inputs.* }}, ${{ context.* }}) are exempt. "
        "When available_recipes is empty (no registry available), no finding is emitted."
    ),
    severity=Severity.ERROR,
)
def _check_unknown_sub_recipe(ctx: ValidationContext) -> list[RuleFinding]:
    if not ctx.available_recipes:
        return []  # Safe fallback: cannot validate without a registry

    findings = []
    for step_name, step in ctx.recipe.steps.items():
        if step.tool != "run_recipe":
            continue
        name_val = step.with_args.get("name", "")
        if not name_val or _TEMPLATE_REF_RE.search(name_val):
            continue  # Template reference — cannot resolve statically
        if name_val not in ctx.available_recipes:
            findings.append(
                RuleFinding(
                    rule="unknown-sub-recipe",
                    severity=Severity.ERROR,
                    step_name=step_name,
                    message=(
                        f"Step '{step_name}' references unknown sub-recipe '{name_val}'. "
                        f"Known recipes: {sorted(ctx.available_recipes)}"
                    ),
                )
            )
    return findings
