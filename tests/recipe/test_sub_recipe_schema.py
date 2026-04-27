"""Tests for sub_recipe and gate schema fields on RecipeStep."""

from __future__ import annotations

import pytest

from autoskillit.recipe.io import _parse_step
from autoskillit.recipe.schema import Recipe, RecipeIngredient, RecipeStep
from autoskillit.recipe.validator import validate_recipe

pytestmark = [pytest.mark.layer("recipe"), pytest.mark.small]


def _make_minimal_recipe(steps: dict, ingredients: dict | None = None) -> Recipe:
    return Recipe(
        name="test-recipe",
        description="Test",
        ingredients=ingredients or {},
        steps={name: RecipeStep(**step_kwargs) for name, step_kwargs in steps.items()},
        kitchen_rules=["no native tools"],
    )


def test_sub_recipe_step_parsed() -> None:
    """RecipeStep with sub_recipe and gate fields is parsed correctly."""
    step = _parse_step(
        {
            "sub_recipe": "test-sub",
            "gate": "flag_mode",
            "on_success": "clone",
        }
    )
    assert step.sub_recipe == "test-sub"
    assert step.gate == "flag_mode"
    assert step.on_success == "clone"


def test_sub_recipe_is_valid_discriminator() -> None:
    """validate_recipe accepts sub_recipe as a valid step discriminator."""
    recipe = Recipe(
        name="test-recipe",
        description="Test",
        ingredients={"flag_mode": RecipeIngredient(description="Gate", default="false")},
        steps={
            "test_entry": RecipeStep(
                sub_recipe="test-sub",
                gate="flag_mode",
                on_success="done",
                on_exhausted="escalate",
            ),
        },
        kitchen_rules=["no native tools"],
    )
    errors = validate_recipe(recipe)
    # Should not error about missing discriminator
    discriminator_errors = [e for e in errors if "must have 'tool'" in e]
    assert not discriminator_errors


def test_sub_recipe_requires_gate() -> None:
    """validate_recipe errors when sub_recipe step has no gate field."""
    recipe = Recipe(
        name="test-recipe",
        description="Test",
        ingredients={"flag_mode": RecipeIngredient(description="Gate", default="false")},
        steps={
            "test_entry": RecipeStep(
                sub_recipe="test-sub",
                on_success="done",
                on_exhausted="escalate",
                # gate is missing
            ),
        },
        kitchen_rules=["no native tools"],
    )
    errors = validate_recipe(recipe)
    assert any("must have a 'gate' field" in e for e in errors)


def test_sub_recipe_gate_must_be_known_ingredient() -> None:
    """validate_recipe errors when gate references an undeclared ingredient."""
    recipe = Recipe(
        name="test-recipe",
        description="Test",
        ingredients={},  # flag_mode NOT declared
        steps={
            "test_entry": RecipeStep(
                sub_recipe="test-sub",
                gate="flag_mode",
                on_success="done",
                on_exhausted="escalate",
            ),
        },
        kitchen_rules=["no native tools"],
    )
    errors = validate_recipe(recipe)
    assert any("undeclared ingredient 'flag_mode'" in e for e in errors)


def test_sub_recipe_discriminator_exclusion() -> None:
    """validate_recipe errors when sub_recipe step also has tool/action/python/constant."""
    recipe = Recipe(
        name="test-recipe",
        description="Test",
        ingredients={"flag_mode": RecipeIngredient(description="Gate", default="false")},
        steps={
            "test_entry": RecipeStep(
                sub_recipe="test-sub",
                gate="flag_mode",
                tool="run_skill",  # conflict!
                on_success="done",
                on_exhausted="escalate",
            ),
        },
        kitchen_rules=["no native tools"],
    )
    errors = validate_recipe(recipe)
    assert any("sub_recipe" in e and "mutually exclusive" in e for e in errors)


def test_sub_recipe_step_requires_on_success() -> None:
    """validate_recipe errors when sub_recipe step has no on_success."""
    recipe = Recipe(
        name="test-recipe",
        description="Test",
        ingredients={"flag_mode": RecipeIngredient(description="Gate", default="false")},
        steps={
            "test_entry": RecipeStep(
                sub_recipe="test-sub",
                gate="flag_mode",
                # on_success missing
                on_exhausted="escalate",
            ),
        },
        kitchen_rules=["no native tools"],
    )
    errors = validate_recipe(recipe)
    assert any("must have 'on_success'" in e for e in errors)
