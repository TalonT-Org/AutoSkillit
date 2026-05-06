from __future__ import annotations

import pytest

from autoskillit.recipe.io import builtin_recipes_dir, load_recipe
from autoskillit.recipe.validator import validate_recipe

pytestmark = [pytest.mark.layer("recipe"), pytest.mark.small]


class TestResearchImplementRecipe:
    @pytest.fixture(scope="module")
    def recipe(self):
        return load_recipe(builtin_recipes_dir() / "research-implement.yaml")

    def test_research_implement_validates_clean(self, recipe) -> None:
        errors = validate_recipe(recipe)
        assert errors == [], f"Validation errors: {errors}"

    def test_research_implement_step_count(self, recipe) -> None:
        assert len(recipe.steps) == 20

    def test_research_implement_header(self, recipe) -> None:
        assert recipe.name == "research-implement"
        assert recipe.recipe_version == "1.0.0"
        assert "research-family" in recipe.categories
        assert "research" in recipe.requires_packs

    def test_research_implement_ingredients(self, recipe) -> None:
        names = set(recipe.ingredients.keys())
        assert {"task", "source_dir", "base_branch", "output_mode", "issue_url"} <= names
        assert {"worktree_path", "research_dir", "experiment_plan"} <= names

    def test_excluded_ingredients_absent(self, recipe) -> None:
        names = set(recipe.ingredients.keys())
        assert "scope_report" not in names
        assert "visualization_plan_path" not in names
        assert "report_plan_path" not in names
        assert "experiment_type" not in names

    def test_no_dangling_upstream_refs(self) -> None:
        path = builtin_recipes_dir() / "research-implement.yaml"
        content = path.read_text()
        assert "context.scope_report" not in content
        assert "context.visualization_plan_path" not in content
        assert "context.report_plan_path" not in content
        assert "context.experiment_type" not in content

    def test_push_branch_routes_to_implement_complete(self, recipe) -> None:
        push_step = recipe.steps["push_branch"]
        assert push_step.on_success == "implement_complete"

    def test_terminal_stops(self, recipe) -> None:
        assert recipe.steps["escalate_stop"].action == "stop"
        assert recipe.steps["implement_complete"].action == "stop"
        assert "${{ context.worktree_path }}" in recipe.steps["implement_complete"].message
        assert "${{ context.report_path }}" in recipe.steps["implement_complete"].message
        assert "${{ context.experiment_results }}" in recipe.steps["implement_complete"].message
