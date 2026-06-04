"""Tests for vis-lens phoropter triple wiring in the research recipe."""

from __future__ import annotations

import pytest

from autoskillit.core.paths import pkg_root
from autoskillit.recipe.io import builtin_recipes_dir, load_recipe

pytestmark = [pytest.mark.layer("recipe"), pytest.mark.small]

RESEARCH_RECIPE_PATH = builtin_recipes_dir() / "research.yaml"


@pytest.fixture(scope="module")
def recipe():
    return load_recipe(RESEARCH_RECIPE_PATH)


def test_vis_dial_step_exists(recipe) -> None:
    assert "vis_dial" in recipe.steps


def test_vis_apply_step_exists(recipe) -> None:
    assert "vis_apply" in recipe.steps


def test_vis_synthesize_step_exists(recipe) -> None:
    assert "vis_synthesize" in recipe.steps


def test_vis_dial_phoropter_family(recipe) -> None:
    """vis-lens steps carry phoropter_family: vis-lens."""
    assert recipe.steps["vis_dial"].phoropter_family == "vis-lens"


def test_vis_apply_phoropter_family(recipe) -> None:
    assert recipe.steps["vis_apply"].phoropter_family == "vis-lens"


def test_vis_synthesize_phoropter_family(recipe) -> None:
    assert recipe.steps["vis_synthesize"].phoropter_family == "vis-lens"


def test_vis_dial_captures_disambiguation_fields(recipe) -> None:
    capture = recipe.steps["vis_dial"].capture
    assert "disambiguation_rule_applied" in capture
    assert "tier_c_lens" in capture
    assert "methodology_tradition" in capture


def test_vis_dial_captures_lens_selection(recipe) -> None:
    """vis_dial must also capture selected_lenses and lens_context_paths for the apply step."""
    capture = recipe.steps["vis_dial"].capture
    assert "selected_lenses" in capture
    assert "lens_context_paths" in capture


def test_vis_apply_uses_capture_list(recipe) -> None:
    step = recipe.steps["vis_apply"]
    assert step.capture_list is not None
    assert len(step.capture_list) > 0


def test_vis_apply_retries_zero(recipe) -> None:
    """capture_list requires retries: 0."""
    assert recipe.steps["vis_apply"].retries == 0


def test_vis_synthesize_captures_paths(recipe) -> None:
    capture = recipe.steps["vis_synthesize"].capture
    assert "visualization_plan_path" in capture
    assert "report_plan_path" in capture
    assert "visualization_plan_trace_path" in capture


def test_vis_dial_on_success_vis_apply(recipe) -> None:
    assert recipe.steps["vis_dial"].on_success == "vis_apply"


def test_vis_apply_on_success_vis_synthesize(recipe) -> None:
    assert recipe.steps["vis_apply"].on_success == "vis_synthesize"


def test_vis_synthesize_on_success_create_worktree(recipe) -> None:
    assert recipe.steps["vis_synthesize"].on_success == "create_worktree"


def test_vis_synthesize_on_failure_escalate_stop(recipe) -> None:
    assert recipe.steps["vis_synthesize"].on_failure == "escalate_stop"


def test_synthesize_go_routes_to_vis_dial(recipe) -> None:
    """synthesize GO verdict must route to vis_dial."""
    step = recipe.steps["synthesize"]
    go_condition = next((c for c in step.on_result.conditions if c.when and "GO" in c.when), None)
    assert go_condition is not None, "Missing GO route in synthesize"
    assert go_condition.route == "vis_dial"


def test_create_worktree_copies_viz_plan(recipe) -> None:
    """create_worktree cmd must still pass visualization_plan_path and report_plan_path."""
    step = recipe.steps["create_worktree"]
    cmd = step.with_args.get("cmd", "")
    assert "visualization_plan_path" in cmd
    assert "report_plan_path" in cmd


def test_select_vis_lenses_and_synthesize_vis_plan_skill_dirs_exist() -> None:
    """Both phoropter skill directories must exist."""
    assert (pkg_root() / "skills_extended" / "select-vis-lenses" / "SKILL.md").exists()
    assert (pkg_root() / "skills_extended" / "synthesize-vis-plan" / "SKILL.md").exists()
