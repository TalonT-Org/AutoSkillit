"""Closure coverage for every bundled recipe's effective execution graph."""

from __future__ import annotations

import re
from itertools import product
from pathlib import Path

import pytest

from autoskillit.core import RECIPE_TERMINAL_TARGETS, FinalizedRecipeProjection
from autoskillit.recipe import list_recipes, load_and_validate
from autoskillit.recipe.io import load_recipe
from tests._tracked_recipes import tracked_recipe_names

pytestmark = [pytest.mark.layer("recipe"), pytest.mark.medium]


_PROJECT_ROOT = Path(__file__).parents[2]
_GUARD_REFERENCE = re.compile(r"inputs\.([A-Za-z_][A-Za-z0-9_]*)")
_RECIPE_PATHS = {info.name: info.path for info in list_recipes(_PROJECT_ROOT).items}


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


def _guard_cases() -> list[pytest.ParameterSet]:
    cases: list[pytest.ParameterSet] = []
    for recipe_name in tracked_recipe_names(_PROJECT_ROOT):
        ingredients = _guard_ingredients(recipe_name)
        for values in product((False, True), repeat=len(ingredients)):
            overrides = {
                ingredient: str(value).lower()
                for ingredient, value in zip(ingredients, values, strict=True)
            }
            case_id = recipe_name
            if overrides:
                case_id = f"{recipe_name}-" + "-".join(
                    f"{name}={value}" for name, value in overrides.items()
                )
            cases.append(pytest.param(recipe_name, overrides, id=case_id))
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


@pytest.mark.parametrize(("recipe_name", "ingredient_overrides"), _guard_cases())
def test_effective_graph_is_closed_for_every_guard_configuration(
    recipe_name: str,
    ingredient_overrides: dict[str, str],
    tmp_path: Path,
) -> None:
    """Every finalized graph is connected and contains only valid route targets."""
    result = load_and_validate(
        recipe_name,
        ingredient_overrides=ingredient_overrides,
        include_finalized_projection=True,
        project_dir=_PROJECT_ROOT,
        temp_dir=tmp_path / recipe_name,
    )

    assert result["valid"], result["errors"]
    projection = result["_finalized_projection"]
    step_names = projection.ordered_step_names
    retained_targets = set(step_names) | RECIPE_TERMINAL_TARGETS
    assert all(edge.target in retained_targets for edge in projection.ordered_flow_edges)
    assert _reachable_steps(step_names, projection) == set(step_names)
    assert result["post_prune_step_names"] == list(step_names)
