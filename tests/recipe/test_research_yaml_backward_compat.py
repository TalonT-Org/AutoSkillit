from __future__ import annotations

import pytest

from autoskillit.recipe.io import builtin_recipes_dir, load_recipe
from autoskillit.recipe.schema import RecipeKind
from autoskillit.recipe.validator import validate_recipe

pytestmark = [pytest.mark.layer("recipe"), pytest.mark.small]


class TestResearchYamlBackwardCompat:
    @pytest.fixture(scope="class")
    def recipe(self):
        return load_recipe(builtin_recipes_dir() / "research.yaml")

    def test_research_yaml_loads_without_error(self, recipe):
        assert recipe is not None

    def test_research_yaml_validates_with_no_errors(self, recipe):
        errors = validate_recipe(recipe)
        assert errors == []

    def test_research_yaml_kind_is_standard(self, recipe):
        assert recipe.kind == RecipeKind.STANDARD
