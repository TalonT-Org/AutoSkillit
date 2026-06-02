"""Verify all run_skill steps declare a model: field across bundled recipes."""

from pathlib import Path

import pytest

from autoskillit.recipe.io import all_validated_recipe_paths, load_recipe

pytestmark = [pytest.mark.layer("recipe"), pytest.mark.medium]

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
_ALL_PATHS = all_validated_recipe_paths(_PROJECT_ROOT)
_BUNDLED_ONLY = [p for p in _ALL_PATHS if "src/autoskillit/recipes" in str(p)]
assert _BUNDLED_ONLY, "no bundled recipes found"


class TestAllRunSkillStepsHaveModel:
    """Every run_skill step must declare model: so the orchestrator can propagate it."""

    @pytest.mark.parametrize("recipe_name", [p.name for p in _BUNDLED_ONLY])
    def test_model_field_is_string(self, recipe_name: str) -> None:
        """model: field must be a string (empty or expression), never None."""
        recipe_path = next(p for p in _BUNDLED_ONLY if p.name == recipe_name)
        recipe = load_recipe(recipe_path)
        for name, step in recipe.steps.items():
            if step.tool == "run_skill":
                assert isinstance(step.model, str), (
                    f"{recipe_name}.{name}: model field should be str, got {type(step.model)}"
                )
