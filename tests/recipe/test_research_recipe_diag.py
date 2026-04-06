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


def test_research_recipe_no_validation_errors(recipe):
    """All routing targets in research.yaml must be valid step names (no dead references).

    Unknown step references are caught by validate_recipe as structural errors — there
    is no separate semantic rule for them. This test is equivalent to the validation
    test above but makes the dead-reference intent explicit.
    """
    errors = validate_recipe(recipe)
    assert not errors, (
        f"research.yaml has validation errors (may include dead step references): {errors}"
    )
