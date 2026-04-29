"""Verify implementation-groups.yaml has arch_lenses ingredient and routing."""

import pytest

from autoskillit.recipe.io import builtin_recipes_dir, load_recipe
from autoskillit.recipe.validator import validate_recipe

pytestmark = [pytest.mark.layer("recipe"), pytest.mark.small]

RECIPE_PATH = builtin_recipes_dir() / "implementation-groups.yaml"


@pytest.fixture(scope="module")
def recipe():
    return load_recipe(RECIPE_PATH)


def test_arch_lenses_ingredient_declared(recipe):
    assert "arch_lenses" in recipe.ingredients


def test_arch_lenses_defaults_to_true(recipe):
    assert recipe.ingredients["arch_lenses"].default == "true"


def test_prepare_pr_routes_to_compose_pr_when_arch_lenses_false(recipe):
    step = recipe.steps["prepare_pr"]
    routes = [c.route for c in step.on_result.conditions if c.when and "prep_path" in c.when]
    assert "compose_pr" in routes


def test_prepare_pr_arch_lenses_route_checks_ingredient(recipe):
    step = recipe.steps["prepare_pr"]
    arch_lens_condition = next(
        (c for c in step.on_result.conditions if c.route == "run_arch_lenses" and c.when),
        None,
    )
    assert arch_lens_condition is not None
    assert "arch_lenses" in arch_lens_condition.when


def test_run_arch_lenses_still_gated_on_open_pr(recipe):
    step = recipe.steps["run_arch_lenses"]
    assert step.skip_when_false == "inputs.open_pr"


def test_recipe_validates_clean(recipe):
    errors = validate_recipe(recipe)
    assert not errors, f"Validation errors: {errors}"
