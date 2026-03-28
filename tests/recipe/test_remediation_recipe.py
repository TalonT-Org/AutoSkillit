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


