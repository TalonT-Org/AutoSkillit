"""Test that _merge_sub_recipe skips hidden sub-recipe ingredients."""
from autoskillit.recipe._api import _merge_sub_recipe
from autoskillit.recipe.schema import Recipe, RecipeIngredient, RecipeStep


def _recipe(ingredients: dict, steps: dict | None = None) -> Recipe:
    return Recipe(
        name="test", description="", ingredients=ingredients,
        steps=steps or {}, kitchen_rules=[], version=None,
    )


def test_merge_sub_recipe_skips_hidden_ingredients():
    """_merge_sub_recipe must not copy hidden sub-recipe ingredients into parent.
    This matches the contract stated in the function's docstring."""
    parent = _recipe(
        {"task": RecipeIngredient(description="User task", required=True)},
        steps={"sub_entry": RecipeStep(sub_recipe="sub", on_success="done")},
    )
    sub = _recipe({
        "internal_flag": RecipeIngredient(
            description="Set by orchestrator", default="false", hidden=True
        ),
        "visible_input": RecipeIngredient(description="User-visible", default="x"),
    })
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
    sub = _recipe({
        "shared_param": RecipeIngredient(description="Shared across sub-recipes", default="v"),
    })
    merged = _merge_sub_recipe(parent, "sub_entry", sub)
    assert "shared_param" in merged.ingredients
