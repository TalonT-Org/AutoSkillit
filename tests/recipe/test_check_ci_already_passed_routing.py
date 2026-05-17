"""Tests verifying the check_ci_already_passed safety net step in CI watch recipes."""

import pytest

from autoskillit.recipe.io import builtin_recipes_dir, load_recipe

pytestmark = [pytest.mark.layer("recipe"), pytest.mark.medium]

RECIPE_NAMES = ["implementation", "remediation", "implementation-groups"]


@pytest.fixture(params=RECIPE_NAMES)
def recipe(request):
    return load_recipe(builtin_recipes_dir() / f"{request.param}.yaml")


def test_check_ci_already_passed_step_exists(recipe):
    """New safety net step is present in each recipe."""
    assert "check_ci_already_passed" in recipe.steps


def test_check_ci_already_passed_routes_to_merge_state_on_success(recipe):
    """conclusion == 'success' routes to check_repo_merge_state."""
    step = recipe.steps["check_ci_already_passed"]
    assert step.on_result is not None
    success_routes = [
        c.route for c in (step.on_result.conditions or []) if c.when and "success" in c.when
    ]
    assert "check_repo_merge_state" in success_routes


def test_failed_routes_to_detect_ci_conflict(recipe):
    """conclusion == 'failure' must route to detect_ci_conflict for remediation."""
    step = recipe.steps["check_ci_already_passed"]
    assert step.on_result is not None
    failed_routes = [
        c.route for c in (step.on_result.conditions or []) if c.when and "failure" in c.when
    ]
    assert "detect_ci_conflict" in failed_routes


def test_pending_routes_back_to_ci_watch(recipe):
    """conclusion == 'timed_out' (still pending) must route back to ci_watch for re-poll."""
    step = recipe.steps["check_ci_already_passed"]
    assert step.on_result is not None
    timed_out_routes = [
        c.route for c in (step.on_result.conditions or []) if c.when and "timed_out" in c.when
    ]
    assert "ci_watch" in timed_out_routes


def test_check_ci_already_passed_fallthrough_routes_to_escalate(recipe):
    """Default (non-matching) on_result condition routes to mark_issue_failed_no_ci."""
    step = recipe.steps["check_ci_already_passed"]
    assert step.on_result is not None
    conditions = step.on_result.conditions or []
    fallthrough_routes = [c.route for c in conditions if not c.when]
    assert "mark_issue_failed_no_ci" in fallthrough_routes


def test_check_ci_already_passed_on_failure_routes_to_escalate(recipe):
    """on_failure routes to mark_issue_failed_no_ci."""
    step = recipe.steps["check_ci_already_passed"]
    assert step.on_failure == "mark_issue_failed_no_ci"


def test_check_ci_already_passed_uses_wait_for_ci(recipe):
    """Step uses wait_for_ci tool (structured MCP tool, not inline shell)."""
    step = recipe.steps["check_ci_already_passed"]
    assert step.tool == "wait_for_ci"


def test_check_ci_already_passed_references_merge_target(recipe):
    """Step references context.merge_target as the branch to poll."""
    step = recipe.steps["check_ci_already_passed"]
    assert "context.merge_target" in step.with_args.get("branch", "")


def test_check_ci_already_passed_has_skip_when_false(recipe):
    """Step only runs when inputs.open_pr is set (no PR context -> no-op)."""
    step = recipe.steps["check_ci_already_passed"]
    assert step.skip_when_false == "inputs.open_pr"


def test_no_direct_escalate_stop_no_ci_callers_except_safety_net(recipe):
    """Only check_ci_already_passed and escalate_stop_no_ci itself reference
    escalate_stop_no_ci."""
    EXCLUDED = {
        "check_ci_already_passed",
        "escalate_stop_no_ci",
        "mark_issue_failed_no_ci",
        "register_clone_no_ci",
    }
    for name, step in recipe.steps.items():
        if name in EXCLUDED:
            continue
        direct_routes = {step.on_success, step.on_failure, step.on_exhausted}
        for cond in step.on_result.conditions if step.on_result else []:
            direct_routes.add(cond.route)
        assert "escalate_stop_no_ci" not in direct_routes, (
            f"Step '{name}' still routes directly to escalate_stop_no_ci"
        )


def test_mark_issue_failed_no_ci_routes_to_register_clone_no_ci(recipe):
    """mark_issue_failed_no_ci calls release_issue with fail_label and routes to register_clone."""
    step = recipe.steps["mark_issue_failed_no_ci"]
    assert step.tool == "release_issue"
    assert step.on_success == "register_clone_no_ci"
    assert step.on_failure == "register_clone_no_ci"
    assert step.with_args.get("fail_label") == "fail"


def test_register_clone_no_ci_routes_to_escalate_stop_no_ci(recipe):
    """register_clone_no_ci registers clone as error and routes to escalate_stop."""
    step = recipe.steps["register_clone_no_ci"]
    assert step.tool == "register_clone_status"
    assert step.on_success == "escalate_stop_no_ci"
    assert step.on_failure == "escalate_stop_no_ci"
    assert step.with_args.get("status") == "error"


def test_no_ci_failure_chain_complete(recipe):
    """Full chain: check_ci_already_passed -> mark_issue_failed -> register_clone -> escalate."""
    ci_step = recipe.steps["check_ci_already_passed"]
    conditions = ci_step.on_result.conditions or []
    fallthrough_routes = [c.route for c in conditions if not c.when]
    assert "mark_issue_failed_no_ci" in fallthrough_routes

    mark_step = recipe.steps["mark_issue_failed_no_ci"]
    assert mark_step.on_success == "register_clone_no_ci"

    reg_step = recipe.steps["register_clone_no_ci"]
    assert reg_step.on_success == "escalate_stop_no_ci"


def test_escalate_stop_message_accurate_for_no_checks_case(recipe):
    """The escalation stop message must not claim 'no CI runs found' unconditionally."""
    step = recipe.steps["escalate_stop_no_ci"]
    msg = (step.message or "").lower()
    assert "no ci runs found" not in msg
