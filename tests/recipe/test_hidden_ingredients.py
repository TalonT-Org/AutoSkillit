"""Tests for hidden: true ingredient behavior."""

from __future__ import annotations

import pytest

from autoskillit.recipe._api import format_ingredients_table
from autoskillit.recipe.schema import Recipe, RecipeIngredient, RecipeStep


def _make_recipe(**ingredients: RecipeIngredient) -> Recipe:
    steps = {
        "do_something": RecipeStep(
            tool="run_cmd",
            with_args={"cmd": "echo hi"},
            on_success="done",
            on_exhausted="escalate",
        )
    }
    return Recipe(
        name="test-recipe",
        description="Test recipe",
        ingredients=ingredients,
        steps=steps,
        kitchen_rules=["do not use native tools"],
    )


def test_hidden_ingredient_parsed() -> None:
    """hidden: true is stored on RecipeIngredient.hidden."""
    from autoskillit.recipe.io import _parse_recipe

    data = {
        "name": "test",
        "description": "test",
        "kitchen_rules": ["no native tools"],
        "ingredients": {
            "sprint_mode": {
                "description": "Enable sprint mode",
                "default": "false",
                "hidden": True,
            }
        },
        "steps": {
            "do_it": {"tool": "run_cmd", "with": {"cmd": "echo hi"}, "on_success": "done"}
        },
    }
    recipe = _parse_recipe(data)
    assert recipe.ingredients["sprint_mode"].hidden is True


def test_hidden_ingredient_default_false() -> None:
    """Ingredients without hidden: are parsed with hidden=False."""
    from autoskillit.recipe.io import _parse_recipe

    data = {
        "name": "test",
        "description": "test",
        "kitchen_rules": ["no native tools"],
        "ingredients": {
            "task": {
                "description": "What to implement",
                "required": True,
            }
        },
        "steps": {
            "do_it": {"tool": "run_cmd", "with": {"cmd": "echo hi"}, "on_success": "done"}
        },
    }
    recipe = _parse_recipe(data)
    assert recipe.ingredients["task"].hidden is False


def test_hidden_ingredient_excluded_from_table() -> None:
    """format_ingredients_table omits hidden ingredients."""
    recipe = _make_recipe(
        sprint_mode=RecipeIngredient(
            description="Enable sprint mode",
            default="false",
            hidden=True,
        ),
        task=RecipeIngredient(
            description="What to implement",
            required=True,
        ),
    )
    table = format_ingredients_table(recipe)
    assert table is not None
    assert "sprint_mode" not in table


def test_non_hidden_ingredient_included_in_table() -> None:
    """Non-hidden ingredients still appear in the table."""
    recipe = _make_recipe(
        sprint_mode=RecipeIngredient(
            description="Enable sprint mode",
            default="false",
            hidden=True,
        ),
        task=RecipeIngredient(
            description="What to implement",
            required=True,
        ),
    )
    table = format_ingredients_table(recipe)
    assert table is not None
    assert "task" in table


def test_all_hidden_ingredients_returns_none() -> None:
    """format_ingredients_table returns None when all ingredients are hidden."""
    recipe = _make_recipe(
        sprint_mode=RecipeIngredient(
            description="Hidden flag",
            default="false",
            hidden=True,
        ),
    )
    table = format_ingredients_table(recipe)
    assert table is None
