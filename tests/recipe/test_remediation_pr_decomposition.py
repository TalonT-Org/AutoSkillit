"""Verify remediation.yaml has the three-step PR decomposition."""

import pytest

from autoskillit.recipe.io import builtin_recipes_dir, load_recipe
from autoskillit.recipe.validator import validate_recipe

pytestmark = [pytest.mark.layer("recipe"), pytest.mark.small]

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
    assert step.on_result is not None
    routes = [c.route for c in step.on_result.conditions if c.when and "prep_path" in c.when]
    assert "run_arch_lenses" in routes


def test_run_arch_lenses_routes_to_compose_on_success(recipe):
    step = recipe.steps["run_arch_lenses"]
    assert step.on_success == "compose_pr"


def test_run_arch_lenses_routes_to_compose_on_failure(recipe):
    step = recipe.steps["run_arch_lenses"]
    assert step.on_failure == "compose_pr"


def test_compose_pr_routes_to_extract_pr_number(recipe):
    step = recipe.steps["compose_pr"]
    assert step.on_success == "extract_pr_number"


def test_prepare_pr_captures_three_context_vars(recipe):
    step = recipe.steps["prepare_pr"]
    capture = step.capture or {}
    assert "prep_path" in capture
    assert "selected_lenses" in capture
    assert "lens_context_paths" in capture


def test_run_arch_lenses_uses_capture_list(recipe):
    step = recipe.steps["run_arch_lenses"]
    assert step.capture_list is not None
    assert "all_diagram_paths" in (step.capture_list or {})


def test_compose_pr_captures_pr_url(recipe):
    step = recipe.steps["compose_pr"]
    assert "pr_url" in (step.capture or {})


def test_arch_lenses_ingredient_declared(recipe):
    assert "arch_lenses" in recipe.ingredients


def test_arch_lenses_defaults_to_true(recipe):
    assert recipe.ingredients["arch_lenses"].default == "true"


def test_arch_lenses_is_not_hidden(recipe):
    assert recipe.ingredients["arch_lenses"].hidden is False


def test_prepare_pr_routes_to_compose_pr_when_arch_lenses_false(recipe):
    step = recipe.steps["prepare_pr"]
    assert step.on_result is not None
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


def test_remediation_recipe_validates_after_decomposition(recipe):
    errors = validate_recipe(recipe)
    assert not errors, f"Validation errors: {errors}"
