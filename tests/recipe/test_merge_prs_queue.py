"""Queue mode structural assertions for the bundled recipes.

Tests that the queue-aware steps added in Part B are present and correctly
wired in merge-prs.yaml, implementation.yaml, and remediation.yaml.
"""

from __future__ import annotations

import pytest

from autoskillit.recipe.io import builtin_recipes_dir, load_recipe
from autoskillit.recipe.validator import validate_recipe

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def pmp_recipe():
    return load_recipe(builtin_recipes_dir() / "merge-prs.yaml")


@pytest.fixture(scope="module")
def impl_recipe():
    return load_recipe(builtin_recipes_dir() / "implementation.yaml")


@pytest.fixture(scope="module")
def remed_recipe():
    return load_recipe(builtin_recipes_dir() / "remediation.yaml")


# ---------------------------------------------------------------------------
# merge-prs.yaml — validate passes
# ---------------------------------------------------------------------------


def test_merge_prs_queue_recipe_is_valid(pmp_recipe) -> None:
    """validate_recipe must pass with no errors after queue steps are added."""
    errors = validate_recipe(pmp_recipe)
    assert errors == [], f"validate_recipe errors: {errors}"


# ---------------------------------------------------------------------------
# merge-prs.yaml — new queue mode steps present
# ---------------------------------------------------------------------------


def test_merge_prs_route_by_queue_mode_exists(pmp_recipe) -> None:
    """route_by_queue_mode step must exist in merge-prs."""
    assert "route_by_queue_mode" in pmp_recipe.steps


def test_merge_prs_route_by_queue_mode_is_route_action(pmp_recipe) -> None:
    """route_by_queue_mode must be an action: route step."""
    step = pmp_recipe.steps["route_by_queue_mode"]
    assert step.action == "route"


def test_merge_prs_enqueue_all_prs_exists(pmp_recipe) -> None:
    """enqueue_all_prs step must exist with tool=run_cmd."""
    assert "enqueue_all_prs" in pmp_recipe.steps
    step = pmp_recipe.steps["enqueue_all_prs"]
    assert step.tool == "run_cmd"


def test_merge_prs_wait_queue_pr_exists(pmp_recipe) -> None:
    """wait_queue_pr step must exist with tool=wait_for_merge_queue."""
    assert "wait_queue_pr" in pmp_recipe.steps
    step = pmp_recipe.steps["wait_queue_pr"]
    assert step.tool == "wait_for_merge_queue"


def test_merge_prs_reenter_queue_exists(pmp_recipe) -> None:
    """reenter_queue step must exist."""
    assert "reenter_queue" in pmp_recipe.steps


def test_merge_prs_next_queue_pr_or_done_is_route_action(pmp_recipe) -> None:
    """next_queue_pr_or_done must be an action: route step."""
    assert "next_queue_pr_or_done" in pmp_recipe.steps
    step = pmp_recipe.steps["next_queue_pr_or_done"]
    assert step.action == "route"


# ---------------------------------------------------------------------------
# merge-prs.yaml — analyze_prs captures queue_mode
# ---------------------------------------------------------------------------


def test_merge_prs_analyze_prs_captures_queue_mode(pmp_recipe) -> None:
    """analyze_prs must capture queue_mode from the skill result."""
    step = pmp_recipe.steps["analyze_prs"]
    assert "queue_mode" in (step.capture or {}), (
        "analyze_prs must capture queue_mode to enable route_by_queue_mode"
    )


def test_merge_prs_analyze_prs_routes_to_route_by_queue_mode(pmp_recipe) -> None:
    """analyze_prs.on_success must route to route_by_queue_mode."""
    step = pmp_recipe.steps["analyze_prs"]
    assert step.on_success == "route_by_queue_mode"


# ---------------------------------------------------------------------------
# merge-prs.yaml — classic path still intact
# ---------------------------------------------------------------------------


def test_merge_prs_classic_path_create_integration_branch_present(pmp_recipe) -> None:
    """create_integration_branch step must still be present (classic path)."""
    assert "create_integration_branch" in pmp_recipe.steps


def test_merge_prs_classic_path_merge_pr_present(pmp_recipe) -> None:
    """merge_pr step must still be present (classic path)."""
    assert "merge_pr" in pmp_recipe.steps


def test_merge_prs_classic_path_push_integration_branch_present(pmp_recipe) -> None:
    """push_integration_branch step must still be present (classic path)."""
    assert "push_integration_branch" in pmp_recipe.steps


# ---------------------------------------------------------------------------
# implementation.yaml — validate passes
# ---------------------------------------------------------------------------


def test_implementation_recipe_is_valid(impl_recipe) -> None:
    """validate_recipe must pass with no errors after queue finale steps are added."""
    errors = validate_recipe(impl_recipe)
    assert errors == [], f"validate_recipe errors: {errors}"


# ---------------------------------------------------------------------------
# implementation.yaml — new queue finale steps present
# ---------------------------------------------------------------------------


def test_implementation_check_merge_queue_exists(impl_recipe) -> None:
    """check_merge_queue step must exist in implementation recipe."""
    assert "check_merge_queue" in impl_recipe.steps


def test_implementation_check_merge_queue_has_skip_when_false(impl_recipe) -> None:
    """check_merge_queue must have skip_when_false: inputs.open_pr."""
    step = impl_recipe.steps["check_merge_queue"]
    assert step.skip_when_false == "inputs.open_pr"


def test_implementation_route_queue_mode_is_route_action(impl_recipe) -> None:
    """route_queue_mode must be an action: route step."""
    assert "route_queue_mode" in impl_recipe.steps
    step = impl_recipe.steps["route_queue_mode"]
    assert step.action == "route"


def test_implementation_enable_auto_merge_is_run_cmd(impl_recipe) -> None:
    """enable_auto_merge must use tool=run_cmd."""
    assert "enable_auto_merge" in impl_recipe.steps
    step = impl_recipe.steps["enable_auto_merge"]
    assert step.tool == "run_cmd"


def test_implementation_wait_for_queue_is_wait_for_merge_queue(impl_recipe) -> None:
    """wait_for_queue must use tool=wait_for_merge_queue."""
    assert "wait_for_queue" in impl_recipe.steps
    step = impl_recipe.steps["wait_for_queue"]
    assert step.tool == "wait_for_merge_queue"


def test_implementation_queue_ejected_fix_exists(impl_recipe) -> None:
    """queue_ejected_fix step must exist."""
    assert "queue_ejected_fix" in impl_recipe.steps


def test_implementation_queue_finale_steps_all_have_skip_when_false(impl_recipe) -> None:
    """All six new queue finale steps must have skip_when_false: inputs.open_pr."""
    finale_steps = [
        "check_merge_queue",
        "route_queue_mode",
        "enable_auto_merge",
        "wait_for_queue",
        "queue_ejected_fix",
        "re_push_queue_fix",
        "reenter_merge_queue",
    ]
    for step_name in finale_steps:
        assert step_name in impl_recipe.steps, f"Missing step: {step_name}"
        step = impl_recipe.steps[step_name]
        assert step.skip_when_false == "inputs.open_pr", (
            f"{step_name}.skip_when_false must be 'inputs.open_pr' "
            f"so the open_pr=false path is unchanged"
        )


def test_implementation_ci_watch_routes_to_check_merge_queue_on_success(impl_recipe) -> None:
    """ci_watch.on_success must route to check_merge_queue."""
    step = impl_recipe.steps["ci_watch"]
    assert step.on_success == "check_merge_queue"


def test_implementation_extract_pr_number_exists(impl_recipe) -> None:
    """extract_pr_number step must exist with tool=run_cmd."""
    assert "extract_pr_number" in impl_recipe.steps
    step = impl_recipe.steps["extract_pr_number"]
    assert step.tool == "run_cmd"


def test_implementation_open_pr_step_routes_to_extract_pr_number(impl_recipe) -> None:
    """open_pr_step.on_success must route to extract_pr_number."""
    step = impl_recipe.steps["open_pr_step"]
    assert step.on_success == "extract_pr_number"


# ---------------------------------------------------------------------------
# remediation.yaml — validate passes
# ---------------------------------------------------------------------------


def test_remediation_recipe_is_valid(remed_recipe) -> None:
    """validate_recipe must pass with no errors after queue finale steps are added."""
    errors = validate_recipe(remed_recipe)
    assert errors == [], f"validate_recipe errors: {errors}"


# ---------------------------------------------------------------------------
# remediation.yaml — new queue finale steps present (identical to implementation)
# ---------------------------------------------------------------------------


def test_remediation_check_merge_queue_exists(remed_recipe) -> None:
    """check_merge_queue step must exist in remediation recipe."""
    assert "check_merge_queue" in remed_recipe.steps


def test_remediation_check_merge_queue_has_skip_when_false(remed_recipe) -> None:
    """check_merge_queue must have skip_when_false: inputs.open_pr."""
    step = remed_recipe.steps["check_merge_queue"]
    assert step.skip_when_false == "inputs.open_pr"


def test_remediation_route_queue_mode_is_route_action(remed_recipe) -> None:
    """route_queue_mode must be an action: route step."""
    assert "route_queue_mode" in remed_recipe.steps
    step = remed_recipe.steps["route_queue_mode"]
    assert step.action == "route"


def test_remediation_enable_auto_merge_is_run_cmd(remed_recipe) -> None:
    """enable_auto_merge must use tool=run_cmd."""
    assert "enable_auto_merge" in remed_recipe.steps
    step = remed_recipe.steps["enable_auto_merge"]
    assert step.tool == "run_cmd"


def test_remediation_wait_for_queue_is_wait_for_merge_queue(remed_recipe) -> None:
    """wait_for_queue must use tool=wait_for_merge_queue."""
    assert "wait_for_queue" in remed_recipe.steps
    step = remed_recipe.steps["wait_for_queue"]
    assert step.tool == "wait_for_merge_queue"


def test_remediation_queue_ejected_fix_exists(remed_recipe) -> None:
    """queue_ejected_fix step must exist."""
    assert "queue_ejected_fix" in remed_recipe.steps


def test_remediation_queue_finale_steps_all_have_skip_when_false(remed_recipe) -> None:
    """All new queue finale steps must have skip_when_false: inputs.open_pr."""
    finale_steps = [
        "check_merge_queue",
        "route_queue_mode",
        "enable_auto_merge",
        "wait_for_queue",
        "queue_ejected_fix",
        "re_push_queue_fix",
        "reenter_merge_queue",
    ]
    for step_name in finale_steps:
        assert step_name in remed_recipe.steps, f"Missing step: {step_name}"
        step = remed_recipe.steps[step_name]
        assert step.skip_when_false == "inputs.open_pr", (
            f"{step_name}.skip_when_false must be 'inputs.open_pr' "
            f"so the open_pr=false path is unchanged"
        )


def test_remediation_ci_watch_routes_to_check_merge_queue_on_success(remed_recipe) -> None:
    """ci_watch.on_success must route to check_merge_queue."""
    step = remed_recipe.steps["ci_watch"]
    assert step.on_success == "check_merge_queue"


def test_remediation_extract_pr_number_exists(remed_recipe) -> None:
    """extract_pr_number step must exist with tool=run_cmd."""
    assert "extract_pr_number" in remed_recipe.steps
    step = remed_recipe.steps["extract_pr_number"]
    assert step.tool == "run_cmd"


def test_remediation_open_pr_step_routes_to_extract_pr_number(remed_recipe) -> None:
    """open_pr_step.on_success must route to extract_pr_number."""
    step = remed_recipe.steps["open_pr_step"]
    assert step.on_success == "extract_pr_number"
