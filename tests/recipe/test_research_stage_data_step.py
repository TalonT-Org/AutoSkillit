"""Tests for stage_data step wiring in the research recipe."""

from __future__ import annotations

import pytest

from autoskillit.recipe.io import builtin_recipes_dir, load_recipe

RESEARCH_RECIPE_PATH = builtin_recipes_dir() / "research.yaml"


@pytest.fixture(scope="module")
def recipe():
    return load_recipe(RESEARCH_RECIPE_PATH)


def test_stage_data_step_exists(recipe) -> None:
    """research.yaml must include a stage_data step."""
    assert "stage_data" in recipe.steps


def test_create_worktree_routes_to_stage_data(recipe) -> None:
    """create_worktree on_success must route to stage_data, not decompose_phases."""
    assert recipe.steps["create_worktree"].on_success == "stage_data"


def test_stage_data_uses_run_skill_tool(recipe) -> None:
    """stage_data step must use the run_skill tool."""
    assert recipe.steps["stage_data"].tool == "run_skill"


def test_stage_data_skill_command_references_stage_data_skill(recipe) -> None:
    """stage_data skill_command must reference the stage-data skill."""
    step = recipe.steps["stage_data"]
    assert "stage-data" in step.with_args["skill_command"]


def test_stage_data_cwd_is_worktree_path(recipe) -> None:
    """stage_data cwd must reference context.worktree_path."""
    step = recipe.steps["stage_data"]
    assert "worktree_path" in step.with_args.get("cwd", "")


def test_stage_data_captures_verdict(recipe) -> None:
    """stage_data must capture the verdict token."""
    step = recipe.steps["stage_data"]
    assert "verdict" in step.capture


def test_stage_data_captures_resource_report(recipe) -> None:
    """stage_data must capture the resource_report token."""
    step = recipe.steps["stage_data"]
    assert "resource_report" in step.capture


def test_stage_data_pass_routes_to_decompose_phases(recipe) -> None:
    """stage_data PASS verdict must route to decompose_phases."""
    step = recipe.steps["stage_data"]
    assert step.on_result is not None
    assert step.on_result.routes["PASS"] == "decompose_phases"


def test_stage_data_warn_routes_to_decompose_phases(recipe) -> None:
    """stage_data WARN verdict must route to decompose_phases."""
    step = recipe.steps["stage_data"]
    assert step.on_result is not None
    assert step.on_result.routes["WARN"] == "decompose_phases"


def test_stage_data_fail_does_not_route_to_decompose_phases(recipe) -> None:
    """stage_data FAIL verdict must not route to decompose_phases."""
    step = recipe.steps["stage_data"]
    assert step.on_result is not None
    assert step.on_result.routes.get("FAIL") != "decompose_phases"


def test_stage_data_on_failure_escalates(recipe) -> None:
    """stage_data on_failure must escalate_stop."""
    step = recipe.steps["stage_data"]
    assert step.on_failure == "escalate_stop"


def test_research_recipe_still_validates(recipe) -> None:
    """research.yaml must pass structural validation after stage_data is added."""
    from autoskillit.recipe.validator import validate_recipe

    errors = validate_recipe(recipe)
    assert errors == [], f"Validation errors: {errors}"
