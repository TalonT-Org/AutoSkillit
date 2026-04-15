import pytest

from autoskillit.recipe.io import builtin_recipes_dir, load_recipe
from autoskillit.recipe.validator import validate_recipe

RESEARCH_RECIPE_PATH = builtin_recipes_dir() / "research.yaml"


@pytest.fixture(scope="module")
def recipe():
    return load_recipe(RESEARCH_RECIPE_PATH)


def test_research_recipe_validates_after_diag_changes(recipe):
    """research.yaml must pass structural validation with new troubleshoot steps."""
    errors = validate_recipe(recipe)
    assert not errors, f"Validation errors: {errors}"


def test_research_recipe_has_troubleshoot_step(recipe):
    """research.yaml must contain the troubleshoot_implement_failure step."""
    step_names = list(recipe.steps.keys())
    assert "troubleshoot_implement_failure" in step_names
    assert "route_implement_failure" in step_names


def test_implement_phase_failure_routes_to_troubleshoot(recipe):
    """implement_phase on_failure must route to troubleshoot_implement_failure."""
    step = recipe.steps["implement_phase"]
    assert step.on_failure == "troubleshoot_implement_failure"


def test_implement_phase_exhausted_routes_to_run_experiment(recipe):
    """implement_phase on_exhausted must route to run_experiment (not escalate)."""
    step = recipe.steps["implement_phase"]
    assert step.on_exhausted == "run_experiment"


def test_implement_phase_uses_implement_experiment(recipe):
    """implement_phase must use implement-experiment, not retry-worktree."""
    step = recipe.steps["implement_phase"]
    skill_cmd = step.with_args.get("skill_command", "")
    assert "implement-experiment" in skill_cmd
    assert "retry-worktree" not in skill_cmd


def test_troubleshoot_step_captures_required_tokens(recipe):
    """troubleshoot_implement_failure must capture is_fixable for downstream routing."""
    step = recipe.steps["troubleshoot_implement_failure"]
    capture = step.capture or {}
    assert "is_fixable" in capture


def test_research_recipe_has_three_pr_prep_steps(recipe):
    """open_research_pr is replaced by three decomposed steps."""
    step_names = list(recipe.steps.keys())
    assert "open_research_pr" not in step_names
    assert "prepare_research_pr" in step_names
    assert "run_experiment_lenses" in step_names
    assert "compose_research_pr" in step_names


def test_push_branch_routes_to_prepare_research_pr(recipe):
    """push_branch.on_success must route to prepare_research_pr."""
    step = recipe.steps["push_branch"]
    assert step.on_success == "prepare_research_pr"


def test_prepare_research_pr_routes_to_run_experiment_lenses(recipe):
    """prepare_research_pr.on_success must route to run_experiment_lenses."""
    step = recipe.steps["prepare_research_pr"]
    assert step.on_success == "run_experiment_lenses"


def test_run_experiment_lenses_routes_to_stage_bundle_on_success(recipe):
    """run_experiment_lenses.on_success routes to stage_bundle."""
    step = recipe.steps["run_experiment_lenses"]
    assert step.on_success == "stage_bundle"


def test_run_experiment_lenses_routes_to_stage_bundle_on_failure(recipe):
    """run_experiment_lenses.on_failure routes to stage_bundle (partial diagrams OK)."""
    step = recipe.steps["run_experiment_lenses"]
    assert step.on_failure == "stage_bundle"


def test_compose_research_pr_routes_to_guard_pr_url(recipe):
    """compose_research_pr.on_success routes to guard_pr_url."""
    step = recipe.steps["compose_research_pr"]
    assert step.on_success == "guard_pr_url"


def test_prepare_research_pr_captures_prep_path(recipe):
    """prepare_research_pr must capture prep_path for compose step."""
    step = recipe.steps["prepare_research_pr"]
    assert "prep_path" in (step.capture or {})


def test_prepare_research_pr_uses_context_experiment_plan(recipe):
    """prepare_research_pr must pass ${{ context.experiment_plan }}, not a hardcoded path."""
    step = recipe.steps["prepare_research_pr"]
    skill_cmd = step.with_args.get("skill_command", "")
    assert "context.experiment_plan" in skill_cmd
    assert ".autoskillit/temp/experiment-plan.md" not in skill_cmd


def test_run_experiment_lenses_has_capture_list_for_diagram_paths(recipe):
    """run_experiment_lenses accumulates diagram paths via capture_list."""
    step = recipe.steps["run_experiment_lenses"]
    assert "all_diagram_paths" in (step.capture_list or {})


def test_stage_bundle_exists(recipe):
    """stage_bundle step must exist in the recipe."""
    assert "stage_bundle" in recipe.steps, "research.yaml must have a stage_bundle step"
