"""Verify all run_skill steps declare a model: field across bundled recipes."""

import pytest

from autoskillit.recipe.io import builtin_recipes_dir, load_recipe

pytestmark = [pytest.mark.layer("recipe"), pytest.mark.small]

RECIPES_REQUIRING_MODEL = [
    "implementation.yaml",
    "remediation.yaml",
    "merge-prs.yaml",
    "planner.yaml",
    "implement-findings.yaml",
]


class TestAllRunSkillStepsHaveModel:
    """Every run_skill step must declare model: so the orchestrator can propagate it."""

    @pytest.mark.parametrize("recipe_name", RECIPES_REQUIRING_MODEL)
    def test_run_skill_steps_have_model_field(self, recipe_name: str) -> None:
        recipe_path = builtin_recipes_dir() / recipe_name
        recipe = load_recipe(recipe_path)
        missing = []
        for name, step in recipe.steps.items():
            if step.tool == "run_skill" and step.model is None:
                missing.append(name)
        assert not missing, f"{recipe_name}: run_skill steps missing model: field: {missing}"

    @pytest.mark.parametrize("recipe_name", RECIPES_REQUIRING_MODEL)
    def test_model_field_is_string(self, recipe_name: str) -> None:
        """model: field must be a string (empty or expression), never None."""
        recipe_path = builtin_recipes_dir() / recipe_name
        recipe = load_recipe(recipe_path)
        for name, step in recipe.steps.items():
            if step.tool == "run_skill":
                assert isinstance(step.model, str), (
                    f"{recipe_name}.{name}: model field should be str, got {type(step.model)}"
                )
