"""Tests for recipe/_api.py internals."""

from __future__ import annotations

# ---------------------------------------------------------------------------
# T5 — _drop_sub_recipe_step uses dataclasses.replace
# ---------------------------------------------------------------------------


def test_drop_sub_recipe_step_preserves_future_fields() -> None:
    """_drop_sub_recipe_step round-trips all Recipe fields (catches future field additions)."""

    from autoskillit.recipe._api import _drop_sub_recipe_step
    from autoskillit.recipe.schema import Recipe, RecipeStep

    recipe = Recipe(
        name="test",
        description="desc",
        summary="sum",
        steps={"placeholder": RecipeStep(sub_recipe="sub"), "other": RecipeStep(action="stop")},
        kitchen_rules=["rule1"],
        version="0.2.0",
        experimental=True,
    )
    result = _drop_sub_recipe_step(recipe, "placeholder")
    assert result.name == "test"
    assert result.summary == "sum"
    assert result.experimental is True
    assert result.kitchen_rules == ["rule1"]
    assert "placeholder" not in result.steps
    assert "other" in result.steps
