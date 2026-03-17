"""Structural tests for remediation.yaml recipe."""

from pathlib import Path

import yaml

RECIPE_PATH = (
    Path(__file__).parent.parent.parent / "src" / "autoskillit" / "recipes" / "remediation.yaml"
)


def test_remediation_recipe_has_release_issue_success_step():
    """remediation.yaml must have a release_issue step on the success path.

    Absence of this step means issues resolved via remediation never get the
    staged label applied.
    """
    recipe = yaml.safe_load(RECIPE_PATH.read_text())
    step_names = [step for step in recipe.get("steps", {})]
    assert any("release_issue" in name and "success" in name for name in step_names), (
        "remediation.yaml is missing a release_issue step on the success path. "
        "Without it, issues are never promoted to staged state after a successful remediation."
    )
