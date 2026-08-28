"""Closure coverage for every bundled recipe's effective execution graph."""

from __future__ import annotations

import re
from concurrent.futures import ProcessPoolExecutor
from itertools import product
from pathlib import Path

import pytest

from autoskillit.core import RECIPE_TERMINAL_TARGETS, FinalizedRecipeProjection
from autoskillit.recipe import RecipeKind, list_recipes, load_and_validate
from autoskillit.recipe.io import builtin_recipes_dir, load_recipe
from tests._tracked_recipes import tracked_recipe_names

pytestmark = [pytest.mark.layer("recipe"), pytest.mark.medium]


_PROJECT_ROOT = Path(__file__).parents[2]
_GUARD_REFERENCE = re.compile(r"inputs\.([A-Za-z_][A-Za-z0-9_]*)")
_BUILTIN_RECIPES_DIR = builtin_recipes_dir().resolve()
_RECIPE_PATHS = {
    info.name: info.path
    for info in list_recipes(_PROJECT_ROOT).items
    if info.path.resolve().is_relative_to(_BUILTIN_RECIPES_DIR)
    and info.kind != RecipeKind.CAMPAIGN
}


def _guard_ingredients(recipe_name: str) -> tuple[str, ...]:
    recipe = load_recipe(_RECIPE_PATHS[recipe_name])
    return tuple(
        sorted(
            {
                match.group(1)
                for step in recipe.steps.values()
                if (match := _GUARD_REFERENCE.fullmatch(step.skip_when_false or ""))
            }
        )
    )


def _guard_cases() -> list[tuple[str, dict[str, str]]]:
    cases: list[tuple[str, dict[str, str]]] = []
    for recipe_name in tracked_recipe_names(_PROJECT_ROOT):
        if recipe_name not in _RECIPE_PATHS:
            continue
        ingredients = _guard_ingredients(recipe_name)
        for values in product((False, True), repeat=len(ingredients)):
            overrides = {
                ingredient: str(value).lower()
                for ingredient, value in zip(ingredients, values, strict=True)
            }
            cases.append((recipe_name, overrides))
    return cases


def _reachable_steps(
    projection_step_names: tuple[str, ...], projection: FinalizedRecipeProjection
) -> set[str]:
    reachable = {projection.entrypoint}
    pending = [projection.entrypoint]
    edges_by_source: dict[str, list[str]] = {}
    for edge in projection.ordered_flow_edges:
        edges_by_source.setdefault(edge.source, []).append(edge.target)

    step_names = set(projection_step_names)
    while pending:
        source = pending.pop()
        for target in edges_by_source.get(source, []):
            if target in step_names and target not in reachable:
                reachable.add(target)
                pending.append(target)
    return reachable


def _validate_guard_case(
    case: tuple[int, str, dict[str, str], str],
) -> tuple[str, tuple[str, ...]]:
    index, recipe_name, ingredient_overrides, temp_root = case
    result = load_and_validate(
        recipe_name,
        ingredient_overrides=ingredient_overrides,
        include_finalized_projection=True,
        project_dir=_PROJECT_ROOT,
        temp_dir=Path(temp_root) / f"{recipe_name}-{index}",
    )

    case_name = recipe_name
    if ingredient_overrides:
        case_name += "-" + "-".join(
            f"{name}={value}" for name, value in ingredient_overrides.items()
        )
    failures: list[str] = []
    if not result["valid"]:
        failures.append(f"invalid: {result['errors']!r}")
    projection = result.get("_finalized_projection")
    if not isinstance(projection, FinalizedRecipeProjection):
        failures.append("missing finalized projection")
        return case_name, tuple(failures)
    step_names = projection.ordered_step_names
    retained_targets = set(step_names) | RECIPE_TERMINAL_TARGETS
    dangling = tuple(
        edge.target
        for edge in projection.ordered_flow_edges
        if edge.target not in retained_targets
    )
    if dangling:
        failures.append(f"dangling targets: {dangling!r}")
    missing = set(step_names) - _reachable_steps(step_names, projection)
    if missing:
        failures.append(f"unreachable steps: {sorted(missing)!r}")
    if result["post_prune_step_names"] != list(step_names):
        failures.append("post-prune names differ from the projection")
    return case_name, tuple(failures)


_ALL_GUARD_CASES = _guard_cases()
_CASE_BATCHES = tuple(
    tuple(
        (index, name, overrides)
        for index, (name, overrides) in enumerate(_ALL_GUARD_CASES)
        if index % 4 == batch
    )
    for batch in range(4)
)


@pytest.mark.parametrize("case_batch", _CASE_BATCHES, ids=lambda batch: f"batch-{batch[0][0]}")
def test_effective_graph_is_closed_for_every_guard_configuration(
    case_batch: tuple[tuple[int, str, dict[str, str]], ...],
    tmp_path: Path,
) -> None:
    """Every guard configuration produces a closed finalized graph."""
    work = tuple((*case, str(tmp_path)) for case in case_batch)
    with ProcessPoolExecutor(max_workers=2) as executor:
        outcomes = tuple(executor.map(_validate_guard_case, work))
    failures = {case_name: errors for case_name, errors in outcomes if errors}
    assert not failures, failures
