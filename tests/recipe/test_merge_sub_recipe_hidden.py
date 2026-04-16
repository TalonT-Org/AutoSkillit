"""Test that _merge_sub_recipe skips hidden sub-recipe ingredients."""

import pytest

from autoskillit.recipe._api import _merge_sub_recipe
from autoskillit.recipe.schema import Recipe, RecipeIngredient, RecipeStep

pytestmark = [pytest.mark.layer("recipe")]


def _recipe(
    ingredients: dict, steps: dict | None = None, requires_packs: list[str] | None = None
) -> Recipe:
    return Recipe(
        name="test",
        description="",
        ingredients=ingredients,
        steps=steps or {},
        kitchen_rules=[],
        version=None,
        requires_packs=requires_packs or [],
    )


def test_merge_sub_recipe_skips_hidden_ingredients():
    """_merge_sub_recipe must not copy hidden sub-recipe ingredients into parent.
    This matches the contract stated in the function's docstring."""
    parent = _recipe(
        {"task": RecipeIngredient(description="User task", required=True)},
        steps={"sub_entry": RecipeStep(sub_recipe="sub", on_success="done")},
    )
    sub = _recipe(
        {
            "internal_flag": RecipeIngredient(
                description="Set by orchestrator", default="false", hidden=True
            ),
            "visible_input": RecipeIngredient(description="User-visible", default="x"),
        }
    )
    merged = _merge_sub_recipe(parent, "sub_entry", sub)
    assert "internal_flag" not in merged.ingredients, (
        "Hidden sub-recipe ingredients must not be merged into the parent"
    )
    assert "visible_input" in merged.ingredients


def test_merge_sub_recipe_keeps_non_hidden_ingredients():
    """Non-hidden sub-recipe ingredients must still be merged."""
    parent = _recipe(
        {},
        steps={"sub_entry": RecipeStep(sub_recipe="sub", on_success="done")},
    )
    sub = _recipe(
        {
            "shared_param": RecipeIngredient(description="Shared across sub-recipes", default="v"),
        }
    )
    merged = _merge_sub_recipe(parent, "sub_entry", sub)
    assert "shared_param" in merged.ingredients


def test_merge_sub_recipe_unions_requires_packs():
    """_merge_sub_recipe unions requires_packs parent-first, no duplicates."""
    parent = _recipe(
        {},
        steps={"sub_entry": RecipeStep(sub_recipe="sub", on_success="done")},
        requires_packs=["github"],
    )
    sub = _recipe(
        {
            "shared_param": RecipeIngredient(description="Shared across sub-recipes", default="v"),
        },
        requires_packs=["research", "github"],  # "github" is a duplicate
    )
    merged = _merge_sub_recipe(parent, "sub_entry", sub)
    assert merged.requires_packs == ["github", "research"]


def test_merge_sub_recipe_requires_packs_empty_sub():
    """When sub-recipe has no requires_packs, parent's list is preserved."""
    parent = _recipe(
        {},
        steps={"sub_entry": RecipeStep(sub_recipe="sub", on_success="done")},
        requires_packs=["telemetry"],
    )
    sub = _recipe({})
    merged = _merge_sub_recipe(parent, "sub_entry", sub)
    assert merged.requires_packs == ["telemetry"]


def test_merge_sub_recipe_requires_packs_empty_parent():
    """When parent has no requires_packs, sub-recipe's list is adopted."""
    parent = _recipe(
        {},
        steps={"sub_entry": RecipeStep(sub_recipe="sub", on_success="done")},
    )
    sub = _recipe({}, requires_packs=["research"])
    merged = _merge_sub_recipe(parent, "sub_entry", sub)
    assert merged.requires_packs == ["research"]
