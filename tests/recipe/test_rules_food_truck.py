"""Tests for food-truck semantic validation rules (rules_food_truck.py)."""

from __future__ import annotations

import pytest

import autoskillit.recipe  # noqa: F401 -- triggers rule registration
from autoskillit.core import Severity
from autoskillit.recipe._analysis import make_validation_context
from autoskillit.recipe.registry import run_semantic_rules
from autoskillit.recipe.schema import Recipe, RecipeKind, RecipeStep

pytestmark = [pytest.mark.layer("recipe"), pytest.mark.small]


def _food_truck_recipe(**kwargs: object) -> Recipe:
    defaults: dict = {
        "name": "my-food-truck",
        "description": "test food truck",
        "kind": RecipeKind.FOOD_TRUCK,
        "steps": {"done": RecipeStep(action="stop", message="sentinel done")},
        "kitchen_rules": ["NEVER"],
    }
    defaults.update(kwargs)
    return Recipe(**defaults)


def _standard_recipe(**kwargs: object) -> Recipe:
    return Recipe(
        name="standard",
        description="standard recipe",
        steps={"done": RecipeStep(action="stop", message="done")},
        kitchen_rules=["NEVER"],
        **kwargs,
    )


def _findings(recipe: Recipe, rule: str, **ctx_kwargs: object) -> list:
    ctx = make_validation_context(recipe, **ctx_kwargs)
    return [f for f in run_semantic_rules(ctx) if f.rule == rule]


# ---------------------------------------------------------------------------
# T8: food-truck-has-sentinel-stop (fires when missing)
# ---------------------------------------------------------------------------


def test_food_truck_has_sentinel_stop_rule_fires_on_missing_sentinel():
    """food-truck recipe with no sentinel in stop message triggers a finding."""
    recipe = _food_truck_recipe(
        steps={"done": RecipeStep(action="stop", message="Promotion complete.")},
    )
    found = _findings(recipe, "food-truck-has-sentinel-stop")
    assert found
    assert found[0].severity == Severity.WARNING


# ---------------------------------------------------------------------------
# T9: food-truck-has-sentinel-stop (passes when present)
# ---------------------------------------------------------------------------


def test_food_truck_has_sentinel_stop_rule_passes_when_present():
    """food-truck recipe with sentinel in stop message produces no findings."""
    recipe = _food_truck_recipe(
        steps={
            "done": RecipeStep(action="stop", message="Done. Emit your L3 sentinel JSON block.")
        },
    )
    found = _findings(recipe, "food-truck-has-sentinel-stop")
    assert found == []


# ---------------------------------------------------------------------------
# T10: food-truck-has-sentinel-stop (skips standard)
# ---------------------------------------------------------------------------


def test_food_truck_has_sentinel_stop_rule_skips_standard():
    """Standard recipe produces no findings for the food-truck sentinel rule."""
    recipe = _standard_recipe()
    found = _findings(recipe, "food-truck-has-sentinel-stop")
    assert found == []
