"""Structural tests for remediation.yaml recipe."""

from pathlib import Path

import pytest

from autoskillit.recipe.io import load_recipe
from autoskillit.recipe.validator import validate_recipe

RECIPE_PATH = (
    Path(__file__).parent.parent.parent / "src" / "autoskillit" / "recipes" / "remediation.yaml"
)


@pytest.fixture(scope="module")
def recipe():
    return load_recipe(RECIPE_PATH)


def test_remediation_recipe_has_release_issue_success_step(recipe):
    """remediation.yaml must have a release_issue step on the success path.

    Absence of this step means issues resolved via remediation never get the
    staged label applied.
    """
    errors = validate_recipe(recipe)
    assert not errors, f"remediation.yaml failed schema validation: {errors}"
    step_names = list(recipe.steps.keys())
    assert any("release_issue" in name and "success" in name for name in step_names), (
        "remediation.yaml is missing a release_issue step on the success path. "
        "Without it, issues are never promoted to staged state after a successful remediation."
    )


# T_REM_LOOP1
def test_check_review_loop_step_exists(recipe) -> None:
    """check_review_loop step must exist in remediation recipe."""
    assert "check_review_loop" in recipe.steps


# T_REM_LOOP2
def test_re_push_review_routes_to_check_review_loop(recipe) -> None:
    """re_push_review on_success must route to check_review_loop in remediation recipe."""
    step = recipe.steps["re_push_review"]
    assert step.on_success == "check_review_loop"


# T_REM_LOOP3
def test_check_review_loop_on_failure_routes_to_ci_watch(recipe) -> None:
    """check_review_loop on_failure must route to ci_watch in remediation recipe."""
    step = recipe.steps["check_review_loop"]
    assert step.on_failure == "ci_watch"
