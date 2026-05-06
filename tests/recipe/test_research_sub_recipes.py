"""Tests for research sub-recipe YAML structure (design, implement, review, archive).

All tests skip when the sub-recipe YAML files do not yet exist in recipes/sub-recipes/.
"""

from __future__ import annotations

import pytest

from autoskillit.recipe.io import builtin_sub_recipes_dir, load_recipe
from autoskillit.recipe.validator import validate_recipe

pytestmark = [pytest.mark.layer("recipe"), pytest.mark.small]


class TestResearchDesignSubRecipe:
    @pytest.fixture(scope="class")
    def recipe(self):
        path = builtin_sub_recipes_dir() / "research-design.yaml"
        if not path.exists():
            pytest.skip(f"{path.name} not yet created")
        return load_recipe(path)

    def test_has_required_ingredients(self, recipe) -> None:
        assert "task" in recipe.ingredients
        assert recipe.ingredients["task"].required is True
        assert "source_dir" in recipe.ingredients
        assert recipe.ingredients["source_dir"].required is True

    def test_has_terminal_step(self, recipe) -> None:
        assert any(s.action == "stop" for s in recipe.steps.values())

    def test_key_steps_capture_required_outputs(self, recipe) -> None:
        assert any("experiment_plan" in s.capture for s in recipe.steps.values())
        assert any("visualization_plan_path" in s.capture for s in recipe.steps.values())

    def test_validates_cleanly(self, recipe) -> None:
        assert validate_recipe(recipe) == []


class TestResearchImplementSubRecipe:
    @pytest.fixture(scope="class")
    def recipe(self):
        path = builtin_sub_recipes_dir() / "research-implement.yaml"
        if not path.exists():
            pytest.skip(f"{path.name} not yet created")
        return load_recipe(path)

    def test_has_required_ingredients(self, recipe) -> None:
        assert "task" in recipe.ingredients
        assert recipe.ingredients["task"].required is True
        assert "source_dir" in recipe.ingredients
        assert recipe.ingredients["source_dir"].required is True

    def test_has_terminal_step(self, recipe) -> None:
        assert any(s.action == "stop" for s in recipe.steps.values())

    def test_key_steps_capture_required_outputs(self, recipe) -> None:
        assert any("worktree_path" in s.capture for s in recipe.steps.values())
        assert any("research_dir" in s.capture for s in recipe.steps.values())

    def test_validates_cleanly(self, recipe) -> None:
        assert validate_recipe(recipe) == []


class TestResearchReviewSubRecipe:
    @pytest.fixture(scope="class")
    def recipe(self):
        path = builtin_sub_recipes_dir() / "research-review.yaml"
        if not path.exists():
            pytest.skip(f"{path.name} not yet created")
        return load_recipe(path)

    def test_has_required_ingredients(self, recipe) -> None:
        assert "task" in recipe.ingredients
        assert recipe.ingredients["task"].required is True
        assert "source_dir" in recipe.ingredients
        assert recipe.ingredients["source_dir"].required is True

    def test_has_terminal_step(self, recipe) -> None:
        assert any(s.action == "stop" for s in recipe.steps.values())

    def test_key_steps_capture_required_outputs(self, recipe) -> None:
        assert any("review_verdict" in s.capture for s in recipe.steps.values())

    def test_validates_cleanly(self, recipe) -> None:
        assert validate_recipe(recipe) == []


class TestResearchArchiveSubRecipe:
    @pytest.fixture(scope="class")
    def recipe(self):
        path = builtin_sub_recipes_dir() / "research-archive.yaml"
        if not path.exists():
            pytest.skip(f"{path.name} not yet created")
        return load_recipe(path)

    def test_has_required_ingredients(self, recipe) -> None:
        assert "worktree_path" in recipe.ingredients
        assert recipe.ingredients["worktree_path"].required is True
        assert "research_dir" in recipe.ingredients
        assert recipe.ingredients["research_dir"].required is True

    def test_has_terminal_step(self, recipe) -> None:
        assert any(s.action == "stop" for s in recipe.steps.values())

    def test_key_steps_capture_required_outputs(self, recipe) -> None:
        assert any("archive_tag" in s.capture for s in recipe.steps.values())

    def test_validates_cleanly(self, recipe) -> None:
        assert validate_recipe(recipe) == []
