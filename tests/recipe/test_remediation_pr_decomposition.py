"""Verify remediation.yaml has the three-step PR decomposition."""
import pytest
from autoskillit.recipe.io import load_recipe, builtin_recipes_dir
from autoskillit.recipe.validator import validate_recipe

RECIPE_PATH = builtin_recipes_dir() / "remediation.yaml"


@pytest.fixture(scope="module")
def recipe():
    return load_recipe(RECIPE_PATH)


def test_remediation_recipe_has_three_pr_steps(recipe):
    step_names = list(recipe.steps.keys())
    assert "open_pr_step" not in step_names
    assert "prepare_pr" in step_names
    assert "run_arch_lenses" in step_names
    assert "compose_pr" in step_names


def test_prepare_pr_routes_to_run_arch_lenses(recipe):
    step = recipe.steps["prepare_pr"]
    assert step.on_success == "run_arch_lenses"


def test_run_arch_lenses_routes_to_compose_on_success(recipe):
    step = recipe.steps["run_arch_lenses"]
    assert step.on_success == "compose_pr"


def test_run_arch_lenses_routes_to_compose_on_failure(recipe):
    step = recipe.steps["run_arch_lenses"]
    assert step.on_failure == "compose_pr"


def test_compose_pr_routes_to_extract_pr_number(recipe):
    step = recipe.steps["compose_pr"]
    assert step.on_success == "extract_pr_number"


def test_remediation_recipe_validates_after_decomposition(recipe):
    errors = validate_recipe(recipe)
    assert not errors, f"Validation errors: {errors}"
