"""Structural tests for remediation.yaml recipe."""

from pathlib import Path

from autoskillit.recipe.io import load_recipe
from autoskillit.recipe.validator import validate_recipe

RECIPE_PATH = (
    Path(__file__).parent.parent.parent / "src" / "autoskillit" / "recipes" / "remediation.yaml"
)


def test_remediation_recipe_has_release_issue_success_step():
    """remediation.yaml must have a release_issue step on the success path.

    Absence of this step means issues resolved via remediation never get the
    staged label applied.
    """
    recipe = load_recipe(RECIPE_PATH)
    errors = validate_recipe(recipe)
    assert not errors, f"remediation.yaml failed schema validation: {errors}"
    step_names = list(recipe.steps.keys())
    assert any("release_issue" in name and "success" in name for name in step_names), (
        "remediation.yaml is missing a release_issue step on the success path. "
        "Without it, issues are never promoted to staged state after a successful remediation."
    )
