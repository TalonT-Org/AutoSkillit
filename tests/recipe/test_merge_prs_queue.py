"""Queue mode structural assertions for the bundled recipes.

Tests that the queue-aware steps added in Part B are present and correctly
wired in merge-prs.yaml, implementation.yaml, and remediation.yaml.
"""

from __future__ import annotations

import re

import pytest

from autoskillit.core import PRState
from autoskillit.recipe.io import builtin_recipes_dir, load_recipe
from autoskillit.recipe.validator import validate_recipe

pytestmark = [pytest.mark.layer("recipe"), pytest.mark.small]

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


@pytest.fixture(scope="module")
def impl_groups_recipe():
    return load_recipe(builtin_recipes_dir() / "implementation-groups.yaml")


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


def test_merge_prs_enqueue_current_pr_exists(pmp_recipe) -> None:
    """enqueue_current_pr step must exist with tool=run_cmd."""
    assert "enqueue_current_pr" in pmp_recipe.steps
    step = pmp_recipe.steps["enqueue_current_pr"]
    assert step.tool == "run_cmd"


def test_merge_prs_wait_queue_pr_exists(pmp_recipe) -> None:
    """wait_queue_pr step must exist with tool=wait_for_merge_queue."""
    assert "wait_queue_pr" in pmp_recipe.steps
    step = pmp_recipe.steps["wait_queue_pr"]
    assert step.tool == "wait_for_merge_queue"


def test_merge_prs_reenter_queue_exists(pmp_recipe) -> None:
    """reenter_queue step must exist."""
    assert "reenter_queue" in pmp_recipe.steps


def test_merge_prs_advance_queue_pr_exists(pmp_recipe) -> None:
    """advance_queue_pr step must exist with tool=run_cmd."""
    assert "advance_queue_pr" in pmp_recipe.steps
    step = pmp_recipe.steps["advance_queue_pr"]
    assert step.tool == "run_cmd"


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
# merge-prs.yaml — sequential enqueue behavioral (Test 1A)
# ---------------------------------------------------------------------------


def test_merge_prs_enqueue_all_prs_removed(pmp_recipe) -> None:
    """enqueue_all_prs batch step must be removed."""
    assert "enqueue_all_prs" not in pmp_recipe.steps


def test_merge_prs_enqueue_current_pr_routes_to_wait(pmp_recipe) -> None:
    """enqueue_current_pr.on_success must route to wait_queue_pr."""
    step = pmp_recipe.steps["enqueue_current_pr"]
    assert step.on_success == "wait_queue_pr"


def test_merge_prs_enqueue_current_pr_references_single_pr(pmp_recipe) -> None:
    """enqueue_current_pr cmd must reference context.current_pr_number (no batch loop)."""
    step = pmp_recipe.steps["enqueue_current_pr"]
    cmd = step.with_args.get("cmd", "")
    assert "context.current_pr_number" in cmd
    assert "while read" not in cmd
    assert "jq -r '.[].number'" not in cmd


def test_merge_prs_get_first_pr_number_captures_pr_number(pmp_recipe) -> None:
    """get_first_pr_number must capture current_pr_number."""
    step = pmp_recipe.steps["get_first_pr_number"]
    assert "current_pr_number" in (step.capture or {})


def test_merge_prs_get_first_pr_number_routes_to_enqueue(pmp_recipe) -> None:
    """get_first_pr_number.on_success must route to enqueue_current_pr."""
    step = pmp_recipe.steps["get_first_pr_number"]
    assert step.on_success == "enqueue_current_pr"


# ---------------------------------------------------------------------------
# merge-prs.yaml — cheap rebase pre-check (Test 1C)
# ---------------------------------------------------------------------------


def test_merge_prs_attempt_cheap_rebase_exists(pmp_recipe) -> None:
    """attempt_cheap_rebase step must exist with tool=run_cmd."""
    assert "attempt_cheap_rebase" in pmp_recipe.steps
    step = pmp_recipe.steps["attempt_cheap_rebase"]
    assert step.tool == "run_cmd"


def test_merge_prs_attempt_cheap_rebase_cmd_uses_rebase(pmp_recipe) -> None:
    """attempt_cheap_rebase cmd must contain git rebase and clean/conflicts output."""
    step = pmp_recipe.steps["attempt_cheap_rebase"]
    cmd = step.with_args.get("cmd", "")
    assert "git rebase" in cmd
    assert "clean" in cmd
    assert "conflicts" in cmd


def test_merge_prs_attempt_cheap_rebase_routing(pmp_recipe) -> None:
    """attempt_cheap_rebase clean routes to push_ejected_fix, conflicts to resolve."""
    step = pmp_recipe.steps["attempt_cheap_rebase"]
    assert step.on_result is not None
    conditions = step.on_result.conditions
    clean_routes = [c for c in conditions if c.when and "clean" in c.when]
    assert clean_routes, "must have a 'clean' condition"
    assert clean_routes[0].route == "push_ejected_fix"
    fallback = [c for c in conditions if c.when is None]
    assert fallback, "must have a fallback condition"
    assert fallback[0].route == "resolve_ejected_conflicts"


def test_merge_prs_get_ejected_routes_to_cheap_rebase(pmp_recipe) -> None:
    """get_ejected_pr_branch.on_success must route to attempt_cheap_rebase."""
    step = pmp_recipe.steps["get_ejected_pr_branch"]
    assert step.on_success == "attempt_cheap_rebase"


def test_merge_prs_checkout_ejected_pr_removed(pmp_recipe) -> None:
    """checkout_ejected_pr step must be removed (consolidated into attempt_cheap_rebase)."""
    assert "checkout_ejected_pr" not in pmp_recipe.steps


# ---------------------------------------------------------------------------
# merge-prs.yaml — CI watch before reenter_queue (Test 1D)
# ---------------------------------------------------------------------------


def test_merge_prs_ci_watch_post_queue_fix_exists(pmp_recipe) -> None:
    """ci_watch_post_queue_fix step must exist in merge-prs.yaml."""
    assert "ci_watch_post_queue_fix" in pmp_recipe.steps


def test_merge_prs_push_ejected_fix_routes_to_ci_watch(pmp_recipe) -> None:
    """push_ejected_fix.on_success must route to ci_watch_post_queue_fix."""
    step = pmp_recipe.steps["push_ejected_fix"]
    assert step.on_success == "ci_watch_post_queue_fix"


def test_merge_prs_ci_watch_routes_to_reenter(pmp_recipe) -> None:
    """ci_watch_post_queue_fix.on_success must route to reenter_queue."""
    step = pmp_recipe.steps["ci_watch_post_queue_fix"]
    assert step.on_success == "reenter_queue"


# ---------------------------------------------------------------------------
# merge-prs.yaml — recipe-level capture for advancement (Test 1E)
# ---------------------------------------------------------------------------


def test_merge_prs_advance_queue_pr_is_run_cmd(pmp_recipe) -> None:
    """advance_queue_pr step must exist with tool=run_cmd."""
    assert "advance_queue_pr" in pmp_recipe.steps
    step = pmp_recipe.steps["advance_queue_pr"]
    assert step.tool == "run_cmd"


def test_merge_prs_next_queue_pr_or_done_removed(pmp_recipe) -> None:
    """next_queue_pr_or_done step must be removed (replaced by advance_queue_pr)."""
    assert "next_queue_pr_or_done" not in pmp_recipe.steps


def test_merge_prs_advance_queue_pr_cmd_references_pr_order(pmp_recipe) -> None:
    """advance_queue_pr cmd must reference pr_order_file and current_pr_number."""
    step = pmp_recipe.steps["advance_queue_pr"]
    cmd = step.with_args.get("cmd", "")
    assert "pr_order_file" in cmd
    assert "current_pr_number" in cmd


def test_merge_prs_advance_queue_pr_captures_pr_number(pmp_recipe) -> None:
    """advance_queue_pr must have a capture block for current_pr_number using | trim."""
    step = pmp_recipe.steps["advance_queue_pr"]
    capture = step.capture or {}
    assert "current_pr_number" in capture
    assert "trim" in capture["current_pr_number"]


def test_merge_prs_advance_queue_pr_routing(pmp_recipe) -> None:
    """advance_queue_pr routes to enqueue_current_pr (default) or collect_and_check_impl_plans."""
    step = pmp_recipe.steps["advance_queue_pr"]
    assert step.on_result is not None
    conditions = step.on_result.conditions
    done_routes = [c for c in conditions if c.when and "done" in c.when]
    assert done_routes, "must have a 'done' condition"
    assert done_routes[0].route == "collect_and_check_impl_plans"
    default_routes = [c for c in conditions if c.when is None]
    assert default_routes, "must have a default route"
    assert default_routes[0].route == "enqueue_current_pr"


# ---------------------------------------------------------------------------
# merge-prs.yaml — new PRState route steps (Test 1F)
# ---------------------------------------------------------------------------


def test_merge_prs_diagnose_queue_ci_exists(pmp_recipe) -> None:
    """diagnose_queue_ci step must exist with tool=run_skill."""
    assert "diagnose_queue_ci" in pmp_recipe.steps
    step = pmp_recipe.steps["diagnose_queue_ci"]
    assert step.tool == "run_skill"


def test_merge_prs_reenroll_stalled_queue_pr_exists(pmp_recipe) -> None:
    """reenroll_stalled_queue_pr step must exist with tool=toggle_auto_merge."""
    assert "reenroll_stalled_queue_pr" in pmp_recipe.steps
    step = pmp_recipe.steps["reenroll_stalled_queue_pr"]
    assert step.tool == "toggle_auto_merge"


def test_merge_prs_reenroll_stalled_routes_to_wait(pmp_recipe) -> None:
    """reenroll_stalled_queue_pr must route back to wait_queue_pr."""
    step = pmp_recipe.steps["reenroll_stalled_queue_pr"]
    assert step.on_success == "wait_queue_pr"


def test_merge_prs_dropped_healthy_routes_to_reenter(pmp_recipe) -> None:
    """dropped_healthy in wait_queue_pr must route to reenter_queue."""
    step = pmp_recipe.steps["wait_queue_pr"]
    assert step.on_result is not None
    dropped_routes = [
        c
        for c in step.on_result.conditions
        if c.when is not None and "dropped_healthy" in c.when and c.route == "reenter_queue"
    ]
    assert dropped_routes, "dropped_healthy must route to reenter_queue"


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


def test_implementation_has_check_repo_merge_state(impl_recipe) -> None:
    """check_repo_merge_state step must exist with correct structure."""
    assert "check_repo_merge_state" in impl_recipe.steps
    step = impl_recipe.steps["check_repo_merge_state"]
    assert step.tool == "check_repo_merge_state"
    assert step.block == "pre_queue_gate"
    assert set(step.capture or {}) >= {
        "queue_available",
        "merge_group_trigger",
        "auto_merge_available",
    }


def test_implementation_pre_queue_gate_routes_to_route_queue_mode(impl_recipe) -> None:
    """check_repo_merge_state.on_success must route to route_queue_mode."""
    step = impl_recipe.steps["check_repo_merge_state"]
    assert step.on_success == "route_queue_mode"


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


def test_queue_ejected_fix_tool_is_run_cmd(impl_recipe) -> None:
    step = impl_recipe.steps["queue_ejected_fix"]
    assert step.tool == "run_cmd", (
        "queue_ejected_fix must be a run_cmd cheap-rebase step, not a run_skill"
    )


def test_queue_ejected_fix_clean_routes_to_re_push(impl_recipe) -> None:
    step = impl_recipe.steps["queue_ejected_fix"]
    clean_route = next(
        (c.route for c in step.on_result.conditions if c.when and "clean" in c.when),
        None,
    )
    assert clean_route == "re_push_queue_fix"


def test_queue_ejected_fix_conflicts_routes_to_resolve_skill(impl_recipe) -> None:
    step = impl_recipe.steps["queue_ejected_fix"]
    # The catch-all (no 'when') must route to the renamed skill step
    fallback_route = next(
        (c.route for c in step.on_result.conditions if not c.when),
        None,
    )
    assert fallback_route == "resolve_queue_merge_conflicts"


def test_queue_ejected_fix_on_failure_routes_to_resolve_skill(impl_recipe) -> None:
    step = impl_recipe.steps["queue_ejected_fix"]
    assert step.on_failure == "resolve_queue_merge_conflicts"


def test_resolve_queue_merge_conflicts_exists_with_run_skill(impl_recipe) -> None:
    assert "resolve_queue_merge_conflicts" in impl_recipe.steps
    step = impl_recipe.steps["resolve_queue_merge_conflicts"]
    assert step.tool == "run_skill"


def test_resolve_queue_merge_conflicts_captures_escalation(impl_recipe) -> None:
    step = impl_recipe.steps["resolve_queue_merge_conflicts"]
    assert "conflict_escalation_required" in step.capture


def test_resolve_queue_merge_conflicts_routes_to_re_push(impl_recipe) -> None:
    step = impl_recipe.steps["resolve_queue_merge_conflicts"]
    fallback_route = next(
        (c.route for c in step.on_result.conditions if not c.when),
        None,
    )
    assert fallback_route == "re_push_queue_fix"


def test_implementation_queue_finale_steps_all_have_skip_when_false(impl_recipe) -> None:
    """All queue finale steps must have skip_when_false: inputs.open_pr."""
    finale_steps = [
        "check_repo_merge_state",
        "route_queue_mode",
        "enable_auto_merge",
        "wait_for_queue",
        "queue_ejected_fix",
        "resolve_queue_merge_conflicts",
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


def test_implementation_ci_watch_routes_to_check_repo_merge_state_on_success(
    impl_recipe,
) -> None:
    """ci_watch.on_success must route to check_repo_merge_state."""
    step = impl_recipe.steps["ci_watch"]
    assert step.on_success == "check_repo_merge_state"


def test_implementation_extract_pr_number_exists(impl_recipe) -> None:
    """extract_pr_number step must exist with tool=run_cmd."""
    assert "extract_pr_number" in impl_recipe.steps
    step = impl_recipe.steps["extract_pr_number"]
    assert step.tool == "run_cmd"


def test_implementation_compose_pr_routes_to_extract_pr_number(impl_recipe) -> None:
    """compose_pr.on_success must route to extract_pr_number."""
    step = impl_recipe.steps["compose_pr"]
    assert step.on_success == "extract_pr_number"


def test_implementation_route_queue_mode_requires_merge_group_trigger(impl_recipe) -> None:
    """route_queue_mode must NOT route to enable_auto_merge without checking merge_group_trigger.

    Specifically, the conditions list must not contain a bare 'queue_available == true'
    → enable_auto_merge without also requiring merge_group_trigger == true.
    """
    step = impl_recipe.steps["route_queue_mode"]
    assert step.action == "route"
    conditions = step.on_result.conditions if step.on_result else []
    # Find any condition that routes to enable_auto_merge
    queue_conditions = [c for c in conditions if c.route == "enable_auto_merge"]
    assert len(queue_conditions) == 1, "Exactly one condition must route to enable_auto_merge"
    cond_when = queue_conditions[0].when or ""
    assert "merge_group_trigger" in cond_when, (
        "The enable_auto_merge route condition must reference merge_group_trigger "
        "to prevent queue enrollment when the CI workflow lacks the trigger"
    )


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


def test_remediation_has_check_repo_merge_state(remed_recipe) -> None:
    """check_repo_merge_state step must exist with correct structure in remediation."""
    assert "check_repo_merge_state" in remed_recipe.steps
    step = remed_recipe.steps["check_repo_merge_state"]
    assert step.tool == "check_repo_merge_state"
    assert step.block == "pre_queue_gate"
    assert set(step.capture or {}) >= {
        "queue_available",
        "merge_group_trigger",
        "auto_merge_available",
    }


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
    """All queue finale steps must have skip_when_false: inputs.open_pr."""
    finale_steps = [
        "check_repo_merge_state",
        "route_queue_mode",
        "enable_auto_merge",
        "wait_for_queue",
        "queue_ejected_fix",
        "resolve_queue_merge_conflicts",
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


def test_remediation_ci_watch_routes_to_check_repo_merge_state_on_success(
    remed_recipe,
) -> None:
    """ci_watch.on_success must route to check_repo_merge_state."""
    step = remed_recipe.steps["ci_watch"]
    assert step.on_success == "check_repo_merge_state"


def test_remediation_extract_pr_number_exists(remed_recipe) -> None:
    """extract_pr_number step must exist with tool=run_cmd."""
    assert "extract_pr_number" in remed_recipe.steps
    step = remed_recipe.steps["extract_pr_number"]
    assert step.tool == "run_cmd"


def test_remediation_compose_pr_routes_to_extract_pr_number(remed_recipe) -> None:
    """compose_pr.on_success must route to extract_pr_number."""
    step = remed_recipe.steps["compose_pr"]
    assert step.on_success == "extract_pr_number"


def test_remediation_route_queue_mode_requires_merge_group_trigger(remed_recipe) -> None:
    """route_queue_mode must NOT route to enable_auto_merge without checking merge_group_trigger.

    Specifically, the conditions list must not contain a bare 'queue_available == true'
    → enable_auto_merge without also requiring merge_group_trigger == true.
    """
    step = remed_recipe.steps["route_queue_mode"]
    conditions = step.on_result.conditions if step.on_result else []
    queue_conditions = [c for c in conditions if c.route == "enable_auto_merge"]
    assert len(queue_conditions) == 1
    cond_when = queue_conditions[0].when or ""
    assert "merge_group_trigger" in cond_when


def test_impl_groups_has_check_repo_merge_state(impl_groups_recipe) -> None:
    """check_repo_merge_state step must exist in implementation-groups recipe."""
    assert "check_repo_merge_state" in impl_groups_recipe.steps
    step = impl_groups_recipe.steps["check_repo_merge_state"]
    assert step.block == "pre_queue_gate"


def test_impl_groups_route_queue_mode_requires_merge_group_trigger(impl_groups_recipe) -> None:
    """route_queue_mode must NOT route to enable_auto_merge without checking merge_group_trigger.

    Specifically, the conditions list must not contain a bare 'queue_available == true'
    → enable_auto_merge without also requiring merge_group_trigger == true.
    """
    step = impl_groups_recipe.steps["route_queue_mode"]
    conditions = step.on_result.conditions if step.on_result else []
    queue_conditions = [c for c in conditions if c.route == "enable_auto_merge"]
    assert len(queue_conditions) == 1
    cond_when = queue_conditions[0].when or ""
    assert "merge_group_trigger" in cond_when


@pytest.fixture(scope="module", params=["impl", "remed", "impl_groups"])
def any_recipe(request, impl_recipe, remed_recipe, impl_groups_recipe):
    return {"impl": impl_recipe, "remed": remed_recipe, "impl_groups": impl_groups_recipe}[
        request.param
    ]


def test_auto_merge_ingredient(any_recipe) -> None:
    assert "auto_merge" in any_recipe.ingredients
    ing = any_recipe.ingredients["auto_merge"]
    assert ing.default == "true"
    assert ing.required is False


def test_route_queue_mode_auto_merge_condition_first(any_recipe) -> None:
    step = any_recipe.steps["route_queue_mode"]
    conds = step.on_result.conditions
    auto_merge_idx = next(i for i, c in enumerate(conds) if c.when and "auto_merge" in c.when)
    queue_available_idx = next(
        i for i, c in enumerate(conds) if c.when and "queue_available" in c.when
    )
    assert auto_merge_idx < queue_available_idx


def test_auto_merge_false_routes_to_register_clone_success(any_recipe) -> None:
    step = any_recipe.steps["route_queue_mode"]
    auto_merge_cond = next(
        c for c in step.on_result.conditions if c.when and "auto_merge" in c.when
    )
    assert auto_merge_cond.when == "${{ inputs.auto_merge }} != 'true'"
    assert auto_merge_cond.route == "register_clone_success"


# ---------------------------------------------------------------------------
# Direct merge fallback — all three affected recipes
# ---------------------------------------------------------------------------


def test_route_queue_mode_default_routes_to_immediate_merge(any_recipe) -> None:
    """Default (fallthrough) condition must route to immediate_merge, not direct_merge."""
    step = any_recipe.steps["route_queue_mode"]
    default_cond = next((c for c in step.on_result.conditions if c.when is None), None)
    assert default_cond is not None, "Expected a default (when=None) condition in route_queue_mode"
    assert default_cond.route == "immediate_merge"


def test_direct_merge_step_exists(any_recipe) -> None:
    assert "direct_merge" in any_recipe.steps
    step = any_recipe.steps["direct_merge"]
    assert step.tool == "run_cmd"


def test_direct_merge_routes_to_wait_for_direct_merge(any_recipe) -> None:
    step = any_recipe.steps["direct_merge"]
    assert step.on_success == "wait_for_direct_merge"


def test_direct_merge_failure_routes_to_release_issue_failure(any_recipe) -> None:
    step = any_recipe.steps["direct_merge"]
    assert step.on_failure == "release_issue_failure"


def test_wait_for_direct_merge_step_exists(any_recipe) -> None:
    assert "wait_for_direct_merge" in any_recipe.steps
    step = any_recipe.steps["wait_for_direct_merge"]
    assert step.tool == "run_cmd"


def test_wait_for_direct_merge_merged_routes_to_success(any_recipe) -> None:
    step = any_recipe.steps["wait_for_direct_merge"]
    merged_cond = next(
        (c for c in step.on_result.conditions if c.when and "merged" in c.when), None
    )
    assert merged_cond is not None, "Expected a 'merged' condition in wait_for_direct_merge"
    assert merged_cond.route == "release_issue_success"


def test_wait_for_direct_merge_closed_routes_to_conflict_fix(any_recipe) -> None:
    step = any_recipe.steps["wait_for_direct_merge"]
    closed_cond = next(
        (c for c in step.on_result.conditions if c.when and "closed" in c.when), None
    )
    assert closed_cond is not None, "Expected a 'closed' condition in wait_for_direct_merge"
    assert closed_cond.route == "direct_merge_conflict_fix"


def test_direct_merge_conflict_fix_exists(any_recipe) -> None:
    assert "direct_merge_conflict_fix" in any_recipe.steps
    step = any_recipe.steps["direct_merge_conflict_fix"]
    assert step.tool == "run_cmd"


def test_direct_merge_conflict_fix_clean_routes_to_re_push(any_recipe) -> None:
    step = any_recipe.steps["direct_merge_conflict_fix"]
    clean_route = next(
        (c.route for c in step.on_result.conditions if c.when and "clean" in c.when),
        None,
    )
    assert clean_route == "re_push_direct_fix"


def test_resolve_direct_merge_conflicts_exists_with_run_skill(any_recipe) -> None:
    assert "resolve_direct_merge_conflicts" in any_recipe.steps
    assert any_recipe.steps["resolve_direct_merge_conflicts"].tool == "run_skill"


def test_re_push_direct_fix_exists(any_recipe) -> None:
    assert "re_push_direct_fix" in any_recipe.steps
    step = any_recipe.steps["re_push_direct_fix"]
    assert step.tool == "push_to_remote"
    assert step.on_success == "redirect_merge"


def test_redirect_merge_step_exists(any_recipe) -> None:
    assert "redirect_merge" in any_recipe.steps
    step = any_recipe.steps["redirect_merge"]
    assert step.tool == "run_cmd"
    assert step.on_success == "wait_for_direct_merge"


def test_direct_merge_steps_have_skip_when_false(any_recipe) -> None:
    new_steps = [
        "direct_merge",
        "wait_for_direct_merge",
        "direct_merge_conflict_fix",
        "resolve_direct_merge_conflicts",
        "re_push_direct_fix",
        "redirect_merge",
    ]
    for step_name in new_steps:
        assert step_name in any_recipe.steps, f"Missing step: {step_name}"
        step = any_recipe.steps[step_name]
        assert step.skip_when_false == "inputs.open_pr", (
            f"{step_name}.skip_when_false must be 'inputs.open_pr'"
        )


# ---------------------------------------------------------------------------
# check_repo_merge_state — consolidated pre-queue gate step (any_recipe)
# ---------------------------------------------------------------------------


def test_check_repo_merge_state_step_exists(any_recipe) -> None:
    """check_repo_merge_state step must exist in all three queue-capable recipes."""
    assert "check_repo_merge_state" in any_recipe.steps


def test_check_repo_merge_state_captures_all_three_fields(any_recipe) -> None:
    step = any_recipe.steps["check_repo_merge_state"]
    assert set(step.capture or {}) >= {
        "queue_available",
        "merge_group_trigger",
        "auto_merge_available",
    }


def test_check_repo_merge_state_routes_to_route_queue_mode_on_success(any_recipe) -> None:
    step = any_recipe.steps["check_repo_merge_state"]
    assert step.on_success == "route_queue_mode"


def test_check_repo_merge_state_has_skip_when_false(any_recipe) -> None:
    step = any_recipe.steps["check_repo_merge_state"]
    assert step.skip_when_false == "inputs.open_pr"


def test_check_repo_merge_state_is_in_pre_queue_gate_block(any_recipe) -> None:
    step = any_recipe.steps["check_repo_merge_state"]
    assert step.block == "pre_queue_gate"


def test_route_queue_mode_has_auto_merge_available_condition(any_recipe) -> None:
    """route_queue_mode must have an explicit condition for auto_merge_available == true."""
    step = any_recipe.steps["route_queue_mode"]
    conds = step.on_result.conditions
    assert any(c.when and "auto_merge_available" in c.when for c in conds)


def test_route_queue_mode_auto_merge_available_routes_to_direct_merge(any_recipe) -> None:
    step = any_recipe.steps["route_queue_mode"]
    cond = next(
        c
        for c in step.on_result.conditions
        if c.when and "auto_merge_available" in c.when and "queue_available" not in c.when
    )
    assert cond.when == "${{ context.auto_merge_available }} == true"
    assert cond.route == "direct_merge"


# ---------------------------------------------------------------------------
# Immediate merge path — new for autoMergeAllowed=false repos
# ---------------------------------------------------------------------------


def test_immediate_merge_step_exists(any_recipe) -> None:
    assert "immediate_merge" in any_recipe.steps
    step = any_recipe.steps["immediate_merge"]
    assert step.tool == "run_cmd"


def test_immediate_merge_uses_squash_without_auto(any_recipe) -> None:
    """immediate_merge must use --squash without --auto."""
    step = any_recipe.steps["immediate_merge"]
    cmd = step.with_args.get("cmd", "")
    assert "--squash" in cmd
    assert "--auto" not in cmd


def test_immediate_merge_routes_to_wait_for_immediate_merge(any_recipe) -> None:
    step = any_recipe.steps["immediate_merge"]
    assert step.on_success == "wait_for_immediate_merge"


def test_immediate_merge_failure_routes_to_release_issue_failure(any_recipe) -> None:
    step = any_recipe.steps["immediate_merge"]
    assert step.on_failure == "release_issue_failure"


def test_wait_for_immediate_merge_step_exists(any_recipe) -> None:
    assert "wait_for_immediate_merge" in any_recipe.steps
    step = any_recipe.steps["wait_for_immediate_merge"]
    assert step.tool == "run_cmd"


def test_wait_for_immediate_merge_merged_routes_to_success(any_recipe) -> None:
    step = any_recipe.steps["wait_for_immediate_merge"]
    merged_cond = next(
        (
            c
            for c in step.on_result.conditions
            if c.when == "${{ result.stdout | trim }} == merged"
        ),
        None,
    )
    assert merged_cond is not None
    assert merged_cond.route == "release_issue_success"


def test_wait_for_immediate_merge_closed_routes_to_conflict_fix(any_recipe) -> None:
    step = any_recipe.steps["wait_for_immediate_merge"]
    closed_cond = next(
        (
            c
            for c in step.on_result.conditions
            if c.when == "${{ result.stdout | trim }} == closed"
        ),
        None,
    )
    assert closed_cond is not None
    assert closed_cond.route == "immediate_merge_conflict_fix"


def test_immediate_merge_conflict_fix_exists(any_recipe) -> None:
    assert "immediate_merge_conflict_fix" in any_recipe.steps
    step = any_recipe.steps["immediate_merge_conflict_fix"]
    assert step.tool == "run_cmd"


def test_immediate_merge_conflict_fix_clean_routes_to_re_push(any_recipe) -> None:
    step = any_recipe.steps["immediate_merge_conflict_fix"]
    clean_route = next(
        (c.route for c in step.on_result.conditions if c.when and "clean" in c.when),
        None,
    )
    assert clean_route == "re_push_immediate_fix"


def test_resolve_immediate_merge_conflicts_exists_with_run_skill(any_recipe) -> None:
    assert "resolve_immediate_merge_conflicts" in any_recipe.steps
    assert any_recipe.steps["resolve_immediate_merge_conflicts"].tool == "run_skill"


def test_re_push_immediate_fix_exists(any_recipe) -> None:
    assert "re_push_immediate_fix" in any_recipe.steps
    step = any_recipe.steps["re_push_immediate_fix"]
    assert step.tool == "push_to_remote"
    assert step.on_success == "remerge_immediate"


def test_remerge_immediate_exists(any_recipe) -> None:
    assert "remerge_immediate" in any_recipe.steps
    step = any_recipe.steps["remerge_immediate"]
    assert step.tool == "run_cmd"
    assert step.on_success == "wait_for_immediate_merge"


def test_remerge_immediate_uses_squash_without_auto(any_recipe) -> None:
    """remerge_immediate must also use --squash without --auto."""
    step = any_recipe.steps["remerge_immediate"]
    cmd = step.with_args.get("cmd", "")
    assert "--squash" in cmd
    assert "--auto" not in cmd


def test_all_immediate_merge_steps_have_skip_when_false(any_recipe) -> None:
    immediate_steps = [
        "immediate_merge",
        "wait_for_immediate_merge",
        "immediate_merge_conflict_fix",
        "resolve_immediate_merge_conflicts",
        "re_push_immediate_fix",
        "remerge_immediate",
    ]
    for step_name in immediate_steps:
        assert step_name in any_recipe.steps, f"Missing step: {step_name}"
        step = any_recipe.steps[step_name]
        assert step.skip_when_false == "inputs.open_pr", (
            f"{step_name}.skip_when_false must be 'inputs.open_pr'"
        )


def test_auto_merge_ingredient_description_updated(any_recipe) -> None:
    ing = any_recipe.ingredients["auto_merge"]
    assert "direct merge" in ing.description.lower() or "direct" in ing.description.lower(), (
        "auto_merge description must mention direct merge as an alternative to queue"
    )


def test_implementation_recipe_still_valid(impl_recipe) -> None:
    errors = validate_recipe(impl_recipe)
    assert errors == [], f"validate_recipe errors: {errors}"


def test_remediation_recipe_still_valid(remed_recipe) -> None:
    errors = validate_recipe(remed_recipe)
    assert errors == [], f"validate_recipe errors: {errors}"


def test_impl_groups_recipe_still_valid(impl_groups_recipe) -> None:
    errors = validate_recipe(impl_groups_recipe)
    assert errors == [], f"validate_recipe errors: {errors}"


# ---------------------------------------------------------------------------
# Queue recipe auto-discovery — any recipe with wait_for_merge_queue routing
# ---------------------------------------------------------------------------


def _discover_queue_recipe_fixtures() -> list[str]:
    """Return fixture names for all bundled recipes with wait_for_merge_queue routing."""
    fixture_map = {
        "implementation": "impl_recipe",
        "remediation": "remed_recipe",
        "implementation-groups": "impl_groups_recipe",
        "merge-prs": "pmp_recipe",
    }
    result = []
    for yaml_path in sorted(builtin_recipes_dir().glob("*.yaml")):
        name = yaml_path.stem
        recipe = load_recipe(yaml_path)
        for step in recipe.steps.values():
            if (
                step.tool == "wait_for_merge_queue"
                and step.on_result is not None
                and getattr(step.on_result, "conditions", None)
            ):
                fixture_name = fixture_map.get(name)
                if fixture_name:
                    result.append(fixture_name)
                break
    return sorted(result)


QUEUE_RECIPES = _discover_queue_recipe_fixtures()

# Family-specific list: impl/remed/impl_groups use release_issue_timeout as
# their queue error escalation step.  Tests that assert step names or routing
# targets specific to this family use this constant instead of QUEUE_RECIPES.
RELEASE_TIMEOUT_RECIPES = ["impl_recipe", "remed_recipe", "impl_groups_recipe"]


# ---------------------------------------------------------------------------
# Gap 1 + Gap 6: ci_watch_post_queue_fix step + ejected_ci_failure routing
# (applies to release_issue_timeout family only)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("recipe_fixture", RELEASE_TIMEOUT_RECIPES)
def test_ci_watch_post_queue_fix_exists(recipe_fixture, request):
    """ci_watch_post_queue_fix step must exist in release_issue_timeout family recipes."""
    recipe = request.getfixturevalue(recipe_fixture)
    assert "ci_watch_post_queue_fix" in recipe.steps, (
        f"ci_watch_post_queue_fix must be a step in {recipe_fixture}"
    )


@pytest.mark.parametrize("recipe_fixture", RELEASE_TIMEOUT_RECIPES)
def test_re_push_queue_fix_routes_to_ci_watch_post_queue_fix(recipe_fixture, request):
    """re_push_queue_fix.on_success must route to ci_watch_post_queue_fix."""
    recipe = request.getfixturevalue(recipe_fixture)
    step = recipe.steps["re_push_queue_fix"]
    assert step.on_success == "ci_watch_post_queue_fix", (
        f"re_push_queue_fix.on_success must be 'ci_watch_post_queue_fix' in {recipe_fixture}"
    )


@pytest.mark.parametrize("recipe_fixture", RELEASE_TIMEOUT_RECIPES)
def test_ci_watch_post_queue_fix_routes_reenter_on_success(recipe_fixture, request):
    """ci_watch_post_queue_fix.on_success must route to reenter_merge_queue."""
    recipe = request.getfixturevalue(recipe_fixture)
    assert "ci_watch_post_queue_fix" in recipe.steps, (
        f"ci_watch_post_queue_fix must be a step in {recipe_fixture}"
    )
    step = recipe.steps["ci_watch_post_queue_fix"]
    assert step.on_success == "reenter_merge_queue", (
        f"ci_watch_post_queue_fix.on_success must be 'reenter_merge_queue' in {recipe_fixture}"
    )


@pytest.mark.parametrize("recipe_fixture", RELEASE_TIMEOUT_RECIPES)
def test_ci_watch_post_queue_fix_routes_detect_ci_conflict_on_failure(recipe_fixture, request):
    """ci_watch_post_queue_fix.on_failure must route to detect_ci_conflict."""
    recipe = request.getfixturevalue(recipe_fixture)
    assert "ci_watch_post_queue_fix" in recipe.steps, (
        f"ci_watch_post_queue_fix must be a step in {recipe_fixture}"
    )
    step = recipe.steps["ci_watch_post_queue_fix"]
    assert step.on_failure == "detect_ci_conflict", (
        f"ci_watch_post_queue_fix.on_failure must be 'detect_ci_conflict' in {recipe_fixture}"
    )


@pytest.mark.parametrize("recipe_fixture", RELEASE_TIMEOUT_RECIPES)
def test_ci_watch_post_queue_fix_uses_wait_for_ci_tool(recipe_fixture, request):
    """ci_watch_post_queue_fix must use the wait_for_ci tool."""
    recipe = request.getfixturevalue(recipe_fixture)
    assert "ci_watch_post_queue_fix" in recipe.steps, (
        f"ci_watch_post_queue_fix must be a step in {recipe_fixture}"
    )
    step = recipe.steps["ci_watch_post_queue_fix"]
    assert step.tool == "wait_for_ci"


@pytest.mark.parametrize("recipe_fixture", RELEASE_TIMEOUT_RECIPES)
def test_ci_watch_post_queue_fix_has_skip_when_false(recipe_fixture, request):
    """ci_watch_post_queue_fix must have skip_when_false: inputs.open_pr."""
    recipe = request.getfixturevalue(recipe_fixture)
    assert "ci_watch_post_queue_fix" in recipe.steps, (
        f"ci_watch_post_queue_fix must be a step in {recipe_fixture}"
    )
    step = recipe.steps["ci_watch_post_queue_fix"]
    assert step.skip_when_false == "inputs.open_pr"


@pytest.mark.parametrize("recipe_fixture", RELEASE_TIMEOUT_RECIPES)
def test_wait_for_queue_routes_ejected_ci_failure_to_diagnose_ci(recipe_fixture, request):
    """wait_for_queue.on_result must route ejected_ci_failure to diagnose_ci."""
    recipe = request.getfixturevalue(recipe_fixture)
    step = recipe.steps["wait_for_queue"]
    assert step.on_result is not None, "wait_for_queue must have on_result"
    conditions = step.on_result.conditions
    ci_failure_routes = [
        c
        for c in conditions
        if c.when is not None and "ejected_ci_failure" in c.when and c.route == "diagnose_ci"
    ]
    assert ci_failure_routes, (
        f"wait_for_queue.on_result must route ejected_ci_failure to diagnose_ci"
        f" in {recipe_fixture}"
    )


@pytest.mark.parametrize("recipe_fixture", RELEASE_TIMEOUT_RECIPES)
def test_wait_for_queue_ejected_ci_failure_precedes_ejected(recipe_fixture, request):
    """ejected_ci_failure route must precede generic ejected in wait_for_queue.on_result."""
    recipe = request.getfixturevalue(recipe_fixture)
    step = recipe.steps["wait_for_queue"]
    assert step.on_result is not None, "wait_for_queue must have on_result"
    conditions = step.on_result.conditions
    whens = [c.when or "" for c in conditions]
    ci_fail_idx = next((i for i, w in enumerate(whens) if "ejected_ci_failure" in w), None)
    ejected_idx = next(
        (i for i, w in enumerate(whens) if w.strip() == "${{ result.pr_state }} == ejected"), None
    )
    assert ci_fail_idx is not None, "ejected_ci_failure route must exist"
    assert ejected_idx is not None, "ejected route must still exist"
    assert ci_fail_idx < ejected_idx, (
        "ejected_ci_failure route must appear before generic ejected route "
        "to prevent CI failure ejections from being handled as conflict ejections"
    )


# ---------------------------------------------------------------------------
# Routing matrix exhaustiveness — queue_available × auto_merge_available
# ---------------------------------------------------------------------------


def test_route_queue_mode_queue_with_auto_routes_to_enable_auto_merge(any_recipe) -> None:
    """queue+auto cell must route to enable_auto_merge."""
    step = any_recipe.steps["route_queue_mode"]
    cond = next(
        c
        for c in step.on_result.conditions
        if c.when
        and "queue_available" in c.when
        and "merge_group_trigger" in c.when
        and "auto_merge_available" in c.when
        and "== true" in c.when.split("auto_merge_available")[1]
    )
    assert cond.route == "enable_auto_merge"


def test_route_queue_mode_queue_without_auto_routes_to_queue_enqueue_no_auto(
    any_recipe,
) -> None:
    """queue+no-auto cell must route to queue_enqueue_no_auto."""
    step = any_recipe.steps["route_queue_mode"]
    cond = next(
        c
        for c in step.on_result.conditions
        if c.when
        and "queue_available" in c.when
        and "merge_group_trigger" in c.when
        and "auto_merge_available" in c.when
        and "== false" in c.when.split("auto_merge_available")[1]
    )
    assert cond.route == "queue_enqueue_no_auto"


def test_route_queue_mode_no_queue_with_auto_routes_to_direct_merge(any_recipe) -> None:
    """no-queue+auto cell must route to direct_merge."""
    step = any_recipe.steps["route_queue_mode"]
    cond = next(
        c
        for c in step.on_result.conditions
        if c.when and "auto_merge_available" in c.when and "queue_available" not in c.when
    )
    assert cond.route == "direct_merge"


def test_route_queue_mode_no_queue_no_auto_falls_through_to_immediate_merge(
    any_recipe,
) -> None:
    """Default (when is None) condition must route to immediate_merge."""
    step = any_recipe.steps["route_queue_mode"]
    cond = next(c for c in step.on_result.conditions if c.when is None)
    assert cond.route == "immediate_merge"


def test_route_queue_mode_never_routes_to_enable_auto_merge_when_auto_unavailable(
    any_recipe,
) -> None:
    """Every condition routing to enable_auto_merge must require auto_merge_available == true."""
    step = any_recipe.steps["route_queue_mode"]
    for cond in step.on_result.conditions:
        if cond.route == "enable_auto_merge":
            assert cond.when is not None
            assert "auto_merge_available" in cond.when
            assert "}} == true" in cond.when.split("auto_merge_available")[1], (
                "enable_auto_merge route must require auto_merge_available == true; "
                f"got: {cond.when}"
            )


def test_enable_auto_merge_route_count(any_recipe) -> None:
    """Exactly one condition must route to enable_auto_merge."""
    step = any_recipe.steps["route_queue_mode"]
    count = sum(1 for c in step.on_result.conditions if c.route == "enable_auto_merge")
    assert count == 1, f"Expected exactly 1 enable_auto_merge route, got {count}"


# ---------------------------------------------------------------------------
# New step: queue_enqueue_no_auto
# ---------------------------------------------------------------------------


def test_queue_enqueue_no_auto_step_exists(any_recipe) -> None:
    assert "queue_enqueue_no_auto" in any_recipe.steps


def test_queue_enqueue_no_auto_is_run_cmd(any_recipe) -> None:
    step = any_recipe.steps["queue_enqueue_no_auto"]
    assert step.tool == "run_cmd"


def test_queue_enqueue_no_auto_uses_plain_squash(any_recipe) -> None:
    """queue_enqueue_no_auto must use --squash without --auto."""
    step = any_recipe.steps["queue_enqueue_no_auto"]
    cmd = step.with_args.get("cmd", "")
    assert "--squash" in cmd
    assert "--auto" not in cmd


def test_queue_enqueue_no_auto_routes_to_wait_for_queue(any_recipe) -> None:
    step = any_recipe.steps["queue_enqueue_no_auto"]
    assert step.on_success == "wait_for_queue"


def test_queue_enqueue_no_auto_failure_routes_to_verify_queue_enrollment(any_recipe) -> None:
    step = any_recipe.steps["queue_enqueue_no_auto"]
    assert step.on_failure == "verify_queue_enrollment"


def test_queue_enqueue_no_auto_skip_when_false(any_recipe) -> None:
    step = any_recipe.steps["queue_enqueue_no_auto"]
    assert step.skip_when_false == "inputs.open_pr"


def test_queue_enqueue_no_auto_step_name(any_recipe) -> None:
    step = any_recipe.steps["queue_enqueue_no_auto"]
    assert step.with_args["step_name"] == "queue_enqueue_no_auto"


# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# T12: Regression — remediation.yaml timeout arm and correct fallback
# ---------------------------------------------------------------------------


def test_remediation_wait_for_queue_has_timeout_arm_and_release_timeout_fallback(
    remed_recipe,
) -> None:
    """remediation.yaml wait_for_queue must have explicit timeout arm and correct fallback."""
    step = remed_recipe.steps["wait_for_queue"]
    assert step.on_result is not None, "wait_for_queue must have on_result"
    conditions = step.on_result.conditions

    # Must have an explicit timeout arm routing to release_issue_timeout
    timeout_conditions = [
        c
        for c in conditions
        if c.when is not None and "timeout" in c.when and c.route == "release_issue_timeout"
    ]
    assert timeout_conditions, (
        "remediation.yaml wait_for_queue must have explicit "
        "'${{ result.pr_state }} == timeout -> release_issue_timeout' arm"
    )

    # Fallback (when=None) must route to release_issue_timeout, not register_clone_success
    fallback_conditions = [c for c in conditions if c.when is None]
    assert fallback_conditions, "wait_for_queue must have a fallback condition (when=None)"
    assert fallback_conditions[0].route == "release_issue_timeout", (
        f"remediation.yaml wait_for_queue fallback must be release_issue_timeout, "
        f"got: {fallback_conditions[0].route!r}"
    )

    # on_failure must route to release_issue_timeout, not register_clone_success
    assert step.on_failure == "release_issue_timeout", (
        f"remediation.yaml wait_for_queue on_failure must be release_issue_timeout, "
        f"got: {step.on_failure!r}"
    )


# ---------------------------------------------------------------------------
# T13: Merge step failure routing — silent success degradation guards
# ---------------------------------------------------------------------------


def test_enable_auto_merge_failure_routes_to_verify_queue_enrollment(any_recipe) -> None:
    """enable_auto_merge on_failure must route to verify_queue_enrollment."""
    step = any_recipe.steps["enable_auto_merge"]
    assert step.on_failure == "verify_queue_enrollment"


def test_verify_queue_enrollment_exists(any_recipe) -> None:
    assert "verify_queue_enrollment" in any_recipe.steps


def test_verify_queue_enrollment_uses_wait_for_merge_queue(any_recipe) -> None:
    step = any_recipe.steps["verify_queue_enrollment"]
    assert step.tool == "wait_for_merge_queue"


def test_verify_queue_enrollment_on_failure_escalates(any_recipe) -> None:
    step = any_recipe.steps["verify_queue_enrollment"]
    assert step.on_failure == "release_issue_timeout"


def test_verify_queue_enrollment_merged_routes_to_release_issue_success(any_recipe) -> None:
    step = any_recipe.steps["verify_queue_enrollment"]
    assert step.on_result is not None
    merged_routes = [c.route for c in step.on_result.conditions if c.when and "merged" in c.when]
    assert merged_routes == ["release_issue_success"]


def test_verify_queue_enrollment_fallback_routes_to_release_issue_timeout(any_recipe) -> None:
    step = any_recipe.steps["verify_queue_enrollment"]
    assert step.on_result is not None
    fallback = [c.route for c in step.on_result.conditions if c.when is None]
    assert fallback == ["release_issue_timeout"]


def test_verify_queue_enrollment_ejected_ci_failure_routes_directly_to_diagnose_ci(
    any_recipe,
) -> None:
    """verify_queue_enrollment must route ejected_ci_failure directly to diagnose_ci.

    A 60s probe that already confirmed CI failure should not feed into a 900s
    wait_for_queue watch that would only route to diagnose_ci anyway.
    """
    step = any_recipe.steps["verify_queue_enrollment"]
    assert step.on_result is not None
    ejected_ci_routes = [
        c.route
        for c in step.on_result.conditions
        if c.when is not None and "ejected_ci_failure" in c.when
    ]
    assert ejected_ci_routes == ["diagnose_ci"], (
        f"verify_queue_enrollment must route ejected_ci_failure directly to diagnose_ci, "
        f"got: {ejected_ci_routes}"
    )


@pytest.mark.parametrize("recipe_name", ["implementation", "remediation", "implementation-groups"])
def test_wait_for_direct_merge_on_failure_routes_to_release_issue_timeout(
    recipe_name: str,
) -> None:
    """wait_for_direct_merge.on_failure must be release_issue_timeout in all three recipes."""
    recipe = load_recipe(builtin_recipes_dir() / f"{recipe_name}.yaml")
    step = recipe.steps["wait_for_direct_merge"]
    assert step.on_failure == "release_issue_timeout", (
        f"{recipe_name}.yaml wait_for_direct_merge.on_failure must be "
        f"'release_issue_timeout', got: {step.on_failure!r}"
    )


# ---------------------------------------------------------------------------
# T9: Full routing parity — every PRState covered (universal + family-specific)
# ---------------------------------------------------------------------------

_REQUIRED_PR_STATE_VALUES = frozenset(s.value for s in PRState if s != PRState.ERROR)
_PR_STATE_WHEN_RE = re.compile(r"\$\{\{\s*result\.pr_state\s*\}\}\s*==\s*(\w+)")


@pytest.mark.parametrize("recipe_fixture", QUEUE_RECIPES)
def test_wait_for_queue_routing_covers_every_pr_state(recipe_fixture, request) -> None:
    """Every wait_for_merge_queue step must cover every non-error PRState."""
    recipe = request.getfixturevalue(recipe_fixture)

    # Find wait_for_merge_queue steps dynamically
    mq_steps = {
        name: step
        for name, step in recipe.steps.items()
        if step.tool == "wait_for_merge_queue"
        and step.on_result is not None
        and step.on_result.conditions
    }
    assert mq_steps, f"{recipe_fixture}: no wait_for_merge_queue step with on_result found"

    for step_name, step in mq_steps.items():
        conditions = step.on_result.conditions

        covered: set[str] = set()
        for c in conditions:
            if c.when is None:
                continue
            m = _PR_STATE_WHEN_RE.search(c.when)
            if m:
                covered.add(m.group(1))

        missing = _REQUIRED_PR_STATE_VALUES - covered
        assert not missing, (
            f"{recipe_fixture}: {step_name}.on_result is missing explicit routing arms "
            f"for PRState values: {sorted(missing)}. Every non-error PRState must have a "
            f"when condition."
        )

        # ejected_ci_failure must precede generic ejected
        whens = [c.when or "" for c in conditions]
        ci_fail_idx = next((i for i, w in enumerate(whens) if "ejected_ci_failure" in w), None)
        ejected_idx = next(
            (i for i, w in enumerate(whens) if w.strip() == "${{ result.pr_state }} == ejected"),
            None,
        )
        assert ci_fail_idx is not None, (
            f"{recipe_fixture}: {step_name} ejected_ci_failure route must exist"
        )
        assert ejected_idx is not None, f"{recipe_fixture}: {step_name} ejected route must exist"
        assert ci_fail_idx < ejected_idx, (
            f"{recipe_fixture}: {step_name} ejected_ci_failure route must appear "
            f"before generic ejected route"
        )


# ---------------------------------------------------------------------------
# T9 family-specific: fallback, on_failure, reenter_merge_queue_cheap
# (release_issue_timeout family only)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("recipe_fixture", RELEASE_TIMEOUT_RECIPES)
def test_wait_for_queue_fallback_routes_to_release_issue_timeout(recipe_fixture, request) -> None:
    """wait_for_queue fallback (when=None) must route to release_issue_timeout."""
    recipe = request.getfixturevalue(recipe_fixture)
    step = recipe.steps["wait_for_queue"]
    assert step.on_result is not None
    fallback_conditions = [c for c in step.on_result.conditions if c.when is None]
    assert fallback_conditions, (
        f"{recipe_fixture}: wait_for_queue.on_result must have a fallback condition"
    )
    assert fallback_conditions[0].route == "release_issue_timeout", (
        f"{recipe_fixture}: wait_for_queue fallback must route to release_issue_timeout, "
        f"got: {fallback_conditions[0].route!r}"
    )


@pytest.mark.parametrize("recipe_fixture", RELEASE_TIMEOUT_RECIPES)
def test_wait_for_queue_on_failure_routes_to_release_issue_timeout(
    recipe_fixture, request
) -> None:
    """wait_for_queue.on_failure must route to release_issue_timeout."""
    recipe = request.getfixturevalue(recipe_fixture)
    step = recipe.steps["wait_for_queue"]
    assert step.on_failure == "release_issue_timeout", (
        f"{recipe_fixture}: wait_for_queue on_failure must be release_issue_timeout, "
        f"got: {step.on_failure!r}"
    )


@pytest.mark.parametrize("recipe_fixture", RELEASE_TIMEOUT_RECIPES)
def test_wait_for_queue_dropped_healthy_routes_to_reenter_merge_queue_cheap(
    recipe_fixture, request
) -> None:
    """dropped_healthy must route to reenter_merge_queue_cheap."""
    recipe = request.getfixturevalue(recipe_fixture)
    assert "reenter_merge_queue_cheap" in recipe.steps, (
        f"{recipe_fixture}: reenter_merge_queue_cheap step must exist"
    )
    step = recipe.steps["wait_for_queue"]
    assert step.on_result is not None
    dropped_routes = [
        c
        for c in step.on_result.conditions
        if c.when is not None
        and "dropped_healthy" in c.when
        and c.route == "reenter_merge_queue_cheap"
    ]
    assert dropped_routes, (
        f"{recipe_fixture}: dropped_healthy must route to reenter_merge_queue_cheap"
    )


# ---------------------------------------------------------------------------
# Auto-discovery structural guards
# ---------------------------------------------------------------------------


def test_auto_discovery_includes_merge_prs() -> None:
    """merge-prs.yaml must appear in the auto-discovered queue recipe list."""
    assert "pmp_recipe" in QUEUE_RECIPES, (
        "merge-prs.yaml (pmp_recipe) must be in QUEUE_RECIPES — if this fails, "
        "someone removed wait_for_merge_queue from merge-prs.yaml"
    )


def test_auto_discovery_matches_known_queue_recipes() -> None:
    """Auto-discovered queue recipes must match the expected set exactly."""
    expected = {"impl_recipe", "remed_recipe", "impl_groups_recipe", "pmp_recipe"}
    actual = set(QUEUE_RECIPES)
    assert actual == expected, (
        f"QUEUE_RECIPES mismatch — expected {sorted(expected)}, got {sorted(actual)}. "
        f"Missing: {sorted(expected - actual)}, Extra: {sorted(actual - expected)}"
    )
