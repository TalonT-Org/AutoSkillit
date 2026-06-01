"""Contract test: implementation.yaml and remediation.yaml must default audit to true."""

from __future__ import annotations

from pathlib import Path

import pytest

from autoskillit.recipe.io import load_recipe

pytestmark = [pytest.mark.layer("recipe"), pytest.mark.small]

_RECIPES_DIR = Path(__file__).resolve().parent.parent.parent / "src" / "autoskillit" / "recipes"


@pytest.mark.parametrize(
    "recipe_name",
    ["implementation", "remediation"],
)
def test_audit_ingredient_defaults_to_true(recipe_name: str):
    """implementation.yaml and remediation.yaml must default inputs.audit_impl to 'true'."""
    recipe = load_recipe(_RECIPES_DIR / f"{recipe_name}.yaml")
    ingredient = recipe.ingredients.get("audit_impl")
    assert ingredient is not None, f"{recipe_name}.yaml has no 'audit_impl' ingredient"
    assert ingredient.default == "true", (
        f"{recipe_name}.yaml: inputs.audit_impl default is {ingredient.default!r}, expected 'true'"
    )
