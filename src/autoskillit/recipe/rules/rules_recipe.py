"""Semantic rules for sub-recipe reference validity and with_args hygiene."""

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
    name="env-key-in-with-args",
    description="step with_args must not contain an env: key (ADR-0003)",
    severity=Severity.ERROR,
)
def _check_env_key_in_with_args(ctx: ValidationContext) -> list[RuleFinding]:
    findings: list[RuleFinding] = []
    for step_name, step in ctx.recipe.steps.items():
        if "env" in step.with_args:
            findings.append(
                RuleFinding(
                    rule="env-key-in-with-args",
                    severity=Severity.ERROR,
                    step_name=step_name,
                    message=(
                        f"step '{step_name}' contains an env: key in with_args. "
                        f"Environment variables are not delivered to headless "
                        f"subprocesses — use positional arguments in skill_command "
                        f"instead. See ADR-0003."
                    ),
                )
            )
    return findings


@semantic_rule(
    name="unknown-sub-recipe",
    description="sub_recipe step must reference a known sub-recipe name",
    severity=Severity.ERROR,
)
def _check_unknown_sub_recipe(ctx: ValidationContext) -> list[RuleFinding]:
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
def _check_circular_sub_recipe(ctx: ValidationContext) -> list[RuleFinding]:
    """Detect circular sub-recipe references using DFS."""
    findings: list[RuleFinding] = []
    _detect_cycles(ctx.recipe, [], findings, project_dir=ctx.project_dir)
    return findings


def _detect_cycles(
    recipe: Recipe,
    chain: list[str],
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
    # Deferred import: recipe.io → recipe.__init__ → recipe.validator → registry
    # → rule modules (including this file) creates a circular import at module load
    # time. This import is intentionally deferred to avoid that cycle.
    from autoskillit.recipe.io import builtin_sub_recipes_dir, find_sub_recipe_by_name, load_recipe

    if _loaded is None:
        _loaded = {}

    chain_set = set(chain)
    for step_name, step in recipe.steps.items():
        if step.sub_recipe is None:
            continue
        sr_name = step.sub_recipe
        if sr_name in chain_set:
            findings.append(
                RuleFinding(
                    rule="circular-sub-recipe",
                    severity=Severity.ERROR,
                    step_name=step_name,
                    message=(
                        f"step '{step_name}': sub_recipe '{sr_name}' creates a circular "
                        f"reference. Chain: {' → '.join(chain)} → {sr_name}"
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
            sub_recipe, chain + [sr_name], findings, project_dir=project_dir, _loaded=_loaded
        )
