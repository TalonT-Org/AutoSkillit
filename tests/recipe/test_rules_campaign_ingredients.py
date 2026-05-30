"""Tests for campaign ingredient validation rules."""

from __future__ import annotations

import pytest

pytestmark = [pytest.mark.layer("recipe"), pytest.mark.small]


def test_auto_injected_campaign_ingredients_includes_config_authoritative_keys():
    """_get_auto_injected_ingredients includes config-authoritative ingredient keys."""
    from autoskillit.recipe.rules.campaign.rules_campaign_ingredients import (
        _get_auto_injected_ingredients,
    )
    from autoskillit.recipe.schema import RecipeIngredient

    ingredients = {
        "base_branch": RecipeIngredient(description="d", authority="config"),
        "task": RecipeIngredient(description="t", required=True),
        "run_name": RecipeIngredient(description="r"),
    }
    result = _get_auto_injected_ingredients(ingredients)
    assert "task" in result
    assert "base_branch" in result
    assert "run_name" not in result
