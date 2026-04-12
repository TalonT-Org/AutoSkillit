"""Tests for plan_visualization step wiring in the research recipe."""

from __future__ import annotations

import pytest

from autoskillit.core.paths import pkg_root
from autoskillit.recipe.io import builtin_recipes_dir, load_recipe

RESEARCH_RECIPE_PATH = builtin_recipes_dir() / "research.yaml"


@pytest.fixture(scope="module")
def recipe():
    return load_recipe(RESEARCH_RECIPE_PATH)


def test_plan_visualization_runs_after_design_review_go(recipe) -> None:
    """review_design GO verdict must route to plan_visualization, not create_worktree."""
    step = recipe.steps["review_design"]
    assert step.on_result is not None
    go_condition = next(c for c in step.on_result.conditions if c.when and "GO" in c.when)
    assert go_condition.route == "plan_visualization", (
        "review_design GO verdict must route to plan_visualization; "
        "direct routing to create_worktree skips visualization plan generation"
    )


def test_plan_visualization_step_exists(recipe) -> None:
    """research.yaml must include a plan_visualization step."""
    assert "plan_visualization" in recipe.steps


def test_plan_visualization_step_routes_to_create_worktree(recipe) -> None:
    """plan_visualization on_success must route to create_worktree."""
    step = recipe.steps["plan_visualization"]
    assert step.on_success == "create_worktree"


def test_plan_visualization_step_captures_paths(recipe) -> None:
    """plan_visualization must capture visualization_plan_path and report_plan_path."""
    step = recipe.steps["plan_visualization"]
    assert "visualization_plan_path" in step.capture
    assert "report_plan_path" in step.capture


def test_create_worktree_copies_viz_plan(recipe) -> None:
    """create_worktree cmd must copy visualization-plan.md and report-plan.md."""
    step = recipe.steps["create_worktree"]
    cmd = step.with_args.get("cmd", "")
    assert "VISUALIZATION_PLAN" in cmd, (
        "create_worktree must reference VISUALIZATION_PLAN context variable"
    )
    assert "REPORT_PLAN" in cmd, "create_worktree must reference REPORT_PLAN context variable"
    assert "visualization-plan.md" in cmd, (
        "create_worktree must copy visualization-plan.md into the research dir"
    )
    assert "report-plan.md" in cmd, (
        "create_worktree must copy report-plan.md into the research dir"
    )


def test_plan_visualization_skill_dir_exists() -> None:
    """src/autoskillit/skills_extended/plan-visualization/SKILL.md must exist."""
    skill_path = pkg_root() / "skills_extended" / "plan-visualization" / "SKILL.md"
    assert skill_path.exists(), f"plan-visualization skill directory not found at {skill_path}"
