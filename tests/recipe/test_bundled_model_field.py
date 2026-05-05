"""Verify all run_skill steps declare a model: field across bundled recipes."""

import pytest

from autoskillit.recipe.io import builtin_recipes_dir, load_recipe

pytestmark = [pytest.mark.layer("recipe"), pytest.mark.small]

_ALL_BUNDLED_RECIPES = [f.name for f in sorted(builtin_recipes_dir().glob("*.yaml"))]


class TestAllRunSkillStepsHaveModel:
    """Every run_skill step must declare model: so the orchestrator can propagate it."""

    @pytest.mark.parametrize("recipe_name", _ALL_BUNDLED_RECIPES)
    def test_model_field_is_string(self, recipe_name: str) -> None:
        """model: field must be a string (empty or expression), never None."""
        recipe_path = builtin_recipes_dir() / recipe_name
        recipe = load_recipe(recipe_path)
        for name, step in recipe.steps.items():
            if step.tool == "run_skill":
                assert isinstance(step.model, str), (
                    f"{recipe_name}.{name}: model field should be str, got {type(step.model)}"
                )
