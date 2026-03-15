"""Semantic rules for sub-recipe reference validity."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from autoskillit.core import Severity, get_logger
from autoskillit.recipe._analysis import ValidationContext
from autoskillit.recipe.registry import RuleFinding, semantic_rule

if TYPE_CHECKING:
    from autoskillit.recipe.schema import Recipe

logger = get_logger(__name__)


@semantic_rule(
    name="unknown-sub-recipe",
    description="sub_recipe step must reference a known sub-recipe name",
    severity=Severity.ERROR,
)
def _unknown_sub_recipe(ctx: ValidationContext) -> list[RuleFinding]:
    if not ctx.available_sub_recipes:
        return []  # fail open when registry is unavailable
    findings: list[RuleFinding] = []
    for step_name, step in ctx.recipe.steps.items():
        if step.sub_recipe is not None and step.sub_recipe not in ctx.available_sub_recipes:
            findings.append(
                RuleFinding(
                    rule="unknown-sub-recipe",
                    severity=Severity.ERROR,
                    step_name=step_name,
                    message=(
                        f"step '{step_name}': sub_recipe '{step.sub_recipe}' is not a "
                        f"known sub-recipe. Known sub-recipes: "
                        f"{sorted(ctx.available_sub_recipes)}"
                    ),
                )
            )
    return findings


@semantic_rule(
    name="circular-sub-recipe",
    description="sub_recipe references must not form a cycle",
    severity=Severity.ERROR,
)
def _circular_sub_recipe(ctx: ValidationContext) -> list[RuleFinding]:
    """Detect circular sub-recipe references using DFS."""
    findings: list[RuleFinding] = []
    _detect_cycles(ctx.recipe, set(), findings, project_dir=ctx.project_dir)
    return findings


def _detect_cycles(
    recipe: Recipe,
    chain: set[str],
    findings: list[RuleFinding],
    *,
    project_dir: Path | None = None,
    _loaded: dict[str, Recipe] | None = None,
) -> None:
    """DFS cycle detection across the sub-recipe reference graph.

    For each sub_recipe step in `recipe`, if the referenced name is already
    in `chain`, a cycle is detected. Otherwise, recurse into the sub-recipe
    if it can be loaded.
    """
    from autoskillit.recipe.io import builtin_sub_recipes_dir, find_sub_recipe_by_name, load_recipe

    if _loaded is None:
        _loaded = {}

    for step_name, step in recipe.steps.items():
        if step.sub_recipe is None:
            continue
        sr_name = step.sub_recipe
        if sr_name in chain:
            findings.append(
                RuleFinding(
                    rule="circular-sub-recipe",
                    severity=Severity.ERROR,
                    step_name=step_name,
                    message=(
                        f"step '{step_name}': sub_recipe '{sr_name}' creates a circular "
                        f"reference. Chain: {' → '.join(sorted(chain))} → {sr_name}"
                    ),
                )
            )
            continue
        # Try to load the sub-recipe to inspect its steps
        if sr_name not in _loaded:
            candidate: Path | None = None
            if project_dir is not None:
                candidate = find_sub_recipe_by_name(sr_name, project_dir)
            if candidate is None:
                candidate = builtin_sub_recipes_dir() / f"{sr_name}.yaml"
                if not candidate.is_file():
                    continue
            try:
                _loaded[sr_name] = load_recipe(candidate)
            except Exception:
                logger.warning("sub_recipe_load_failed", name=sr_name, exc_info=True)
                continue
        sub_recipe = _loaded[sr_name]
        _detect_cycles(
            sub_recipe, chain | {sr_name}, findings, project_dir=project_dir, _loaded=_loaded
        )
