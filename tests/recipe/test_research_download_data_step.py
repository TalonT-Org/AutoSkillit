"""Tests for download_data step wiring in the research recipe."""

from __future__ import annotations

import pytest

from autoskillit.recipe.io import builtin_recipes_dir, load_recipe
from autoskillit.recipe.validator import validate_recipe_structure

pytestmark = [pytest.mark.layer("recipe"), pytest.mark.small]

RESEARCH_RECIPE_PATH = builtin_recipes_dir() / "research.yaml"


@pytest.fixture(scope="module")
def recipe():
    return load_recipe(RESEARCH_RECIPE_PATH)


def test_download_data_step_exists(recipe) -> None:
    """research.yaml must include a download_data step."""
    assert "download_data" in recipe.steps


def test_download_data_uses_run_skill_tool(recipe) -> None:
    """download_data step must use the run_skill tool."""
    assert recipe.steps["download_data"].tool == "run_skill"


def test_download_data_skill_command_references_download_data_skill(recipe) -> None:
    """download_data skill_command must reference the download-data skill."""
    step = recipe.steps["download_data"]
    assert "download-data" in step.with_args["skill_command"]


def test_download_data_skill_command_passes_experiment_plan(recipe) -> None:
    """download_data skill_command must reference experiment_plan."""
    step = recipe.steps["download_data"]
    assert "experiment_plan" in step.with_args["skill_command"]


def test_download_data_cwd_is_worktree_path(recipe) -> None:
    """download_data cwd must reference context.worktree_path."""
    step = recipe.steps["download_data"]
    assert "worktree_path" in step.with_args.get("cwd", "")


def test_download_data_captures_verdict(recipe) -> None:
    """download_data must capture the verdict token."""
    step = recipe.steps["download_data"]
    assert "verdict" in step.capture


def test_download_data_captures_download_report(recipe) -> None:
    """download_data must capture the download_report token."""
    step = recipe.steps["download_data"]
    assert "download_report" in step.capture


def test_download_data_pass_routes_to_setup_environment(recipe) -> None:
    """download_data PASS verdict must route to setup_environment."""
    step = recipe.steps["download_data"]
    assert step.on_result is not None
    assert step.on_result.routes["PASS"] == "setup_environment"


def test_download_data_fail_routes_to_escalate_stop(recipe) -> None:
    """download_data FAIL verdict must route to escalate_stop."""
    step = recipe.steps["download_data"]
    assert step.on_result is not None
    assert step.on_result.routes["FAIL"] == "escalate_stop"


def test_download_data_on_failure_escalates(recipe) -> None:
    """download_data on_failure must escalate_stop."""
    step = recipe.steps["download_data"]
    assert step.on_failure == "escalate_stop"


def test_download_data_stale_threshold_is_14400(recipe) -> None:
    """download_data stale_threshold must be 14400 (4 hours)."""
    step = recipe.steps["download_data"]
    assert step.stale_threshold == 14400


def test_download_data_idle_output_timeout_is_0(recipe) -> None:
    """download_data idle_output_timeout must be 0 (disabled)."""
    step = recipe.steps["download_data"]
    assert step.idle_output_timeout == 0


def test_download_data_retries_is_1(recipe) -> None:
    """download_data retries must be 1."""
    step = recipe.steps["download_data"]
    assert step.retries == 1


def test_download_data_on_exhausted_escalates(recipe) -> None:
    """download_data on_exhausted must escalate_stop."""
    step = recipe.steps["download_data"]
    assert step.on_exhausted == "escalate_stop"


def test_research_recipe_still_validates(recipe) -> None:
    """research.yaml must pass structural validation after download_data is added."""
    errors = validate_recipe_structure(recipe)
    assert errors == [], f"Validation errors: {errors}"
