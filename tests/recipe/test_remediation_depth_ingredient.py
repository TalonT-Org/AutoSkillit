"""Tests for the remediation recipe depth ingredient and investigate step updates."""

import pytest

from autoskillit.core.paths import pkg_root
from autoskillit.recipe.io import load_recipe
from autoskillit.recipe.schema import Recipe

pytestmark = [pytest.mark.layer("recipe"), pytest.mark.small]


@pytest.fixture(scope="module")
def remediation_recipe() -> Recipe:
    return load_recipe(pkg_root() / "recipes" / "remediation.yaml")


def test_remediation_has_depth_ingredient(remediation_recipe: Recipe) -> None:
    """remediation.yaml must declare a 'depth' ingredient."""
    assert "depth" in remediation_recipe.ingredients, (
        "remediation.yaml must contain a 'depth' ingredient for investigation depth mode"
    )


def test_remediation_depth_ingredient_is_hidden(remediation_recipe: Recipe) -> None:
    """The depth ingredient must have hidden: true."""
    ing = remediation_recipe.ingredients["depth"]
    assert ing.hidden is True, (
        "remediation.yaml depth ingredient must have 'hidden: true' — "
        "it is a pipeline-internal ingredient not exposed to end users"
    )


def test_remediation_depth_ingredient_defaults_to_standard(remediation_recipe: Recipe) -> None:
    """The depth ingredient must default to 'standard'."""
    ing = remediation_recipe.ingredients["depth"]
    assert ing.default == "standard", (
        "remediation.yaml depth ingredient must have default='standard' "
        "so the recipe uses standard investigation mode unless explicitly overridden"
    )


def test_remediation_depth_ingredient_is_string_type(remediation_recipe: Recipe) -> None:
    """The depth ingredient description must reference depth or mode."""
    ing = remediation_recipe.ingredients["depth"]
    assert ing.description is not None, (
        "remediation.yaml depth ingredient must have a description"
    )
    desc_lower = ing.description.lower()
    assert "depth" in desc_lower or "mode" in desc_lower, (
        "remediation.yaml depth ingredient description must contain 'depth' or 'mode'"
    )


def test_remediation_investigate_step_has_depth_conditional(remediation_recipe: Recipe) -> None:
    """The investigate step skill_command must include the --depth deep conditional."""
    step = remediation_recipe.steps.get("investigate")
    assert step is not None, "remediation.yaml must contain an 'investigate' step"
    skill_command = step.with_args.get("skill_command", "")
    assert "--depth deep" in skill_command, (
        "remediation.yaml investigate step skill_command must contain '--depth deep' "
        "conditional expression for when inputs.depth == 'deep'"
    )


def test_remediation_investigate_step_has_model_field(remediation_recipe: Recipe) -> None:
    """The investigate step must have a 'model' field."""
    step = remediation_recipe.steps.get("investigate")
    assert step is not None, "remediation.yaml must contain an 'investigate' step"
    assert step.model is not None, (
        "remediation.yaml investigate step must include a 'model' field to set the "
        "main session model based on depth"
    )


def test_remediation_investigate_step_model_uses_opus_for_deep(remediation_recipe: Recipe) -> None:
    """The investigate step model expression must reference opus[1m] for deep mode."""
    step = remediation_recipe.steps.get("investigate")
    assert step is not None, "remediation.yaml must contain an 'investigate' step"
    model_expr = step.model or ""
    assert "opus[1m]" in model_expr, (
        "remediation.yaml investigate step model expression must reference 'opus[1m]' "
        "for deep mode — the main session uses Opus for deep investigation"
    )


def test_remediation_investigate_step_has_on_context_limit(remediation_recipe: Recipe) -> None:
    """The investigate step must have on_context_limit routing."""
    step = remediation_recipe.steps.get("investigate")
    assert step is not None, "remediation.yaml must contain an 'investigate' step"
    assert step.on_context_limit is not None, (
        "remediation.yaml investigate step must have 'on_context_limit' to route to "
        "release_issue_failure when the deep mode session exhausts context"
    )
    assert step.on_context_limit == "release_issue_failure", (
        "remediation.yaml investigate step on_context_limit must route to "
        "'release_issue_failure'"
    )
