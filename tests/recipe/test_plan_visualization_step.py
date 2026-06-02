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


def test_plan_visualization_step_removed(recipe) -> None:
    """plan_visualization must no longer exist — replaced by phoropter triple."""
    assert "plan_visualization" not in recipe.steps


def test_dial_step_exists(recipe) -> None:
    assert "dial" in recipe.steps


def test_apply_step_exists(recipe) -> None:
    assert "apply" in recipe.steps


def test_synthesize_step_exists(recipe) -> None:
    assert "synthesize" in recipe.steps


def test_dial_phoropter_family(recipe) -> None:
    assert recipe.steps["dial"].phoropter_family == "vis-lens"


def test_apply_phoropter_family(recipe) -> None:
    assert recipe.steps["apply"].phoropter_family == "vis-lens"


def test_synthesize_phoropter_family(recipe) -> None:
    assert recipe.steps["synthesize"].phoropter_family == "vis-lens"


def test_dial_captures_disambiguation_fields(recipe) -> None:
    capture = recipe.steps["dial"].capture
    assert "disambiguation_rule_applied" in capture
    assert "tier_c_lens" in capture
    assert "methodology_tradition" in capture


def test_dial_captures_lens_selection(recipe) -> None:
    """dial must also capture selected_lenses and lens_context_paths for the apply step."""
    capture = recipe.steps["dial"].capture
    assert "selected_lenses" in capture
    assert "lens_context_paths" in capture


def test_apply_uses_capture_list(recipe) -> None:
    step = recipe.steps["apply"]
    assert step.capture_list is not None
    assert len(step.capture_list) > 0


def test_apply_retries_zero(recipe) -> None:
    """capture_list requires retries: 0."""
    assert recipe.steps["apply"].retries == 0


def test_synthesize_captures_paths(recipe) -> None:
    capture = recipe.steps["synthesize"].capture
    assert "visualization_plan_path" in capture
    assert "report_plan_path" in capture
    assert "visualization_plan_trace_path" in capture


def test_dial_on_success_apply(recipe) -> None:
    assert recipe.steps["dial"].on_success == "apply"


def test_apply_on_success_synthesize(recipe) -> None:
    assert recipe.steps["apply"].on_success == "synthesize"


def test_synthesize_on_success_create_worktree(recipe) -> None:
    assert recipe.steps["synthesize"].on_success == "create_worktree"


def test_synthesize_on_failure_escalate_stop(recipe) -> None:
    assert recipe.steps["synthesize"].on_failure == "escalate_stop"


def test_review_design_go_routes_to_dial(recipe) -> None:
    """review_design GO verdict must route to dial (not plan_visualization)."""
    step = recipe.steps["review_design"]
    go_condition = next((c for c in step.on_result.conditions if c.when and "GO" in c.when), None)
    assert go_condition is not None, "Missing GO route in review_design"
    assert go_condition.route == "dial"


def test_create_worktree_copies_viz_plan(recipe) -> None:
    """create_worktree cmd must still pass visualization_plan_path and report_plan_path."""
    step = recipe.steps["create_worktree"]
    cmd = step.with_args.get("cmd", "")
    assert "visualization_plan_path" in cmd
    assert "report_plan_path" in cmd


def test_plan_visualization_skill_dir_exists() -> None:
    """plan-visualization skill directory must still exist (not removed by this WP)."""
    skill_path = pkg_root() / "skills_extended" / "plan-visualization" / "SKILL.md"
    assert skill_path.exists()


def test_select_vis_lenses_skill_dir_exists() -> None:
    skill_path = pkg_root() / "skills_extended" / "select-vis-lenses" / "SKILL.md"
    assert skill_path.exists()


def test_synthesize_vis_plan_skill_dir_exists() -> None:
    skill_path = pkg_root() / "skills_extended" / "synthesize-vis-plan" / "SKILL.md"
    assert skill_path.exists()
