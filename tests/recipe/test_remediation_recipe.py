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


def test_remediation_re_push_has_force_true(recipe):
    """re_push step in remediation.yaml must have force='true'."""
    assert "re_push" in recipe.steps
    step = recipe.steps["re_push"]
    assert step.tool == "push_to_remote"
    assert step.with_args.get("force") == "true", (
        "re_push must include force='true' — post-rebase force push required"
    )


def test_remediation_re_push_queue_fix_has_force_true(recipe):
    """re_push_queue_fix step in remediation.yaml must have force='true'."""
    assert "re_push_queue_fix" in recipe.steps
    step = recipe.steps["re_push_queue_fix"]
    assert step.tool == "push_to_remote"
    assert step.with_args.get("force") == "true", (
        "re_push_queue_fix must include force='true' — post-rebase force push required"
    )


def test_remediation_re_push_direct_fix_has_force_true(recipe):
    """re_push_direct_fix step in remediation.yaml must have force='true'."""
    assert "re_push_direct_fix" in recipe.steps
    step = recipe.steps["re_push_direct_fix"]
    assert step.tool == "push_to_remote"
    assert step.with_args.get("force") == "true", (
        "re_push_direct_fix must include force='true' — post-rebase force push required"
    )


def test_remediation_re_push_immediate_fix_has_force_true(recipe):
    """re_push_immediate_fix step in remediation.yaml must have force='true'."""
    assert "re_push_immediate_fix" in recipe.steps
    step = recipe.steps["re_push_immediate_fix"]
    assert step.tool == "push_to_remote"
    assert step.with_args.get("force") == "true", (
        "re_push_immediate_fix must include force='true' — post-rebase force push required"
    )
