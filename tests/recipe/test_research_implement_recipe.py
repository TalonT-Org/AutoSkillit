from __future__ import annotations

import pytest

from autoskillit.recipe.io import builtin_recipes_dir, load_recipe
from autoskillit.recipe.schema import Recipe
from autoskillit.recipe.validator import validate_recipe_structure

pytestmark = [pytest.mark.layer("recipe"), pytest.mark.small]


class TestResearchImplementRecipe:
    @pytest.fixture(scope="module")
    def recipe(self):
        return load_recipe(builtin_recipes_dir() / "research-implement.yaml")

    def test_research_implement_validates_clean(self, recipe) -> None:
        errors = validate_recipe_structure(recipe)
        assert errors == [], f"Validation errors: {errors}"

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

    def test_research_implement_has_retry_delay_steps(self, recipe) -> None:
        """research-implement.yaml must have the retry delay gate and sleep step."""
        assert "route_implement_retry_delay" in recipe.steps
        assert "implement_retry_delay" in recipe.steps
        step = recipe.steps["implement_retry_delay"]
        assert step.tool == "run_cmd"
        assert "sleep" in step.with_args.get("cmd", "")

    def test_research_implement_has_run_retry_delay_steps(self, recipe) -> None:
        """research-implement.yaml must have the run retry delay gate and sleep step."""
        assert "route_run_retry_delay" in recipe.steps
        assert "run_retry_delay" in recipe.steps
        step = recipe.steps["run_retry_delay"]
        assert step.tool == "run_cmd"
        assert "sleep" in step.with_args.get("cmd", "")


class TestResearchImplementDownloadData:
    """Tests for download_data step in research-implement.yaml (T3)."""

    @pytest.fixture(scope="class")
    def recipe(self) -> Recipe:
        return load_recipe(builtin_recipes_dir() / "research-implement.yaml")

    def test_download_data_step_exists(self, recipe: Recipe) -> None:
        """research-implement.yaml must include a download_data step."""
        assert "download_data" in recipe.steps

    def test_download_data_pass_routes_to_decompose_phases(self, recipe: Recipe) -> None:
        """download_data PASS verdict must route to decompose_phases."""
        step = recipe.steps["download_data"]
        assert step.on_result is not None
        assert step.on_result.routes["PASS"] == "decompose_phases"

    def test_download_data_warn_routes_to_decompose_phases(self, recipe: Recipe) -> None:
        """download_data WARN verdict must route to decompose_phases."""
        step = recipe.steps["download_data"]
        assert step.on_result is not None
        assert step.on_result.routes["WARN"] == "decompose_phases"

    def test_download_data_fail_routes_to_escalate_stop(self, recipe: Recipe) -> None:
        """download_data FAIL verdict must route to escalate_stop."""
        step = recipe.steps["download_data"]
        assert step.on_result is not None
        assert step.on_result.routes["FAIL"] == "escalate_stop"

    def test_download_data_stale_threshold(self, recipe: Recipe) -> None:
        """download_data stale_threshold must be 14400 (4 hours)."""
        step = recipe.steps["download_data"]
        assert step.stale_threshold == 14400

    def test_download_data_idle_output_timeout(self, recipe: Recipe) -> None:
        """download_data idle_output_timeout must be 0 (disabled)."""
        step = recipe.steps["download_data"]
        assert step.idle_output_timeout == 0

    def test_stage_data_pass_routes_to_download_data(self, recipe: Recipe) -> None:
        """stage_data PASS verdict must route to download_data."""
        step = recipe.steps["stage_data"]
        assert step.on_result is not None
        assert step.on_result.routes["PASS"] == "download_data"

    def test_stage_data_warn_routes_to_download_data(self, recipe: Recipe) -> None:
        """stage_data WARN verdict must route to download_data."""
        step = recipe.steps["stage_data"]
        assert step.on_result is not None
        assert step.on_result.routes["WARN"] == "download_data"
