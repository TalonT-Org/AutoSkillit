"""Release timeout and retry logic tests for queue-capable recipes that use
register_clone_unconfirmed as the queue error escalation step
(implementation, remediation, implementation-groups)."""

from __future__ import annotations

import pytest

pytestmark = [pytest.mark.layer("recipe"), pytest.mark.small]

RELEASE_TIMEOUT_RECIPES = ["impl_recipe", "remed_recipe", "impl_groups_recipe"]


# ---------------------------------------------------------------------------
# ci_watch_post_queue_fix step + ejected_ci_failure routing
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("recipe_fixture", RELEASE_TIMEOUT_RECIPES)
def test_ci_watch_post_queue_fix_exists(recipe_fixture, request):
    """ci_watch_post_queue_fix step must exist in register_clone_unconfirmed family recipes."""
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
    """ci_watch_post_queue_fix on_result success must route to reenter_merge_queue."""
    recipe = request.getfixturevalue(recipe_fixture)
    step = recipe.steps["ci_watch_post_queue_fix"]
    assert step.on_result is not None, (
        f"ci_watch_post_queue_fix must use on_result routing in {recipe_fixture}"
    )
    success_routes = [
        c.route for c in step.on_result.conditions if c.when and "'success'" in c.when
    ]
    assert "reenter_merge_queue" in success_routes, (
        f"ci_watch_post_queue_fix on_result success must route to 'reenter_merge_queue'"
        f" in {recipe_fixture}"
    )


@pytest.mark.parametrize("recipe_fixture", RELEASE_TIMEOUT_RECIPES)
def test_ci_watch_post_queue_fix_routes_detect_ci_conflict_on_failure(recipe_fixture, request):
    """ci_watch_post_queue_fix.on_failure must route to detect_ci_conflict."""
    recipe = request.getfixturevalue(recipe_fixture)
    step = recipe.steps["ci_watch_post_queue_fix"]
    assert step.on_failure == "detect_ci_conflict", (
        f"ci_watch_post_queue_fix.on_failure must be 'detect_ci_conflict' in {recipe_fixture}"
    )


@pytest.mark.parametrize("recipe_fixture", RELEASE_TIMEOUT_RECIPES)
def test_ci_watch_post_queue_fix_uses_wait_for_ci_tool(recipe_fixture, request):
    """ci_watch_post_queue_fix must use the wait_for_ci tool."""
    recipe = request.getfixturevalue(recipe_fixture)
    step = recipe.steps["ci_watch_post_queue_fix"]
    assert step.tool == "wait_for_ci"


@pytest.mark.parametrize("recipe_fixture", RELEASE_TIMEOUT_RECIPES)
def test_ci_watch_post_queue_fix_has_skip_when_false(recipe_fixture, request):
    """ci_watch_post_queue_fix must have skip_when_false: inputs.open_pr."""
    recipe = request.getfixturevalue(recipe_fixture)
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
# T12: Regression — remediation.yaml timeout arm and correct fallback
# ---------------------------------------------------------------------------


def test_remediation_wait_for_queue_has_timeout_arm_and_release_timeout_fallback(
    remed_recipe,
) -> None:
    """remediation.yaml wait_for_queue must have explicit timeout arm and correct fallback."""
    step = remed_recipe.steps["wait_for_queue"]
    assert step.on_result is not None, "wait_for_queue must have on_result"
    conditions = step.on_result.conditions

    # Must have an explicit timeout arm routing to register_clone_unconfirmed
    timeout_conditions = [
        c
        for c in conditions
        if c.when is not None and "timeout" in c.when and c.route == "register_clone_unconfirmed"
    ]
    assert timeout_conditions, (
        "remediation.yaml wait_for_queue must have explicit "
        "'${{ result.pr_state }} == timeout -> register_clone_unconfirmed' arm"
    )

    # Fallback (when=None) must route to register_clone_unconfirmed, not register_clone_success
    fallback_conditions = [c for c in conditions if c.when is None]
    assert fallback_conditions, "wait_for_queue must have a fallback condition (when=None)"
    assert fallback_conditions[0].route == "register_clone_unconfirmed", (
        f"remediation.yaml wait_for_queue fallback must be register_clone_unconfirmed, "
        f"got: {fallback_conditions[0].route!r}"
    )

    # on_failure must route to register_clone_unconfirmed, not register_clone_success
    assert step.on_failure == "register_clone_unconfirmed", (
        f"remediation.yaml wait_for_queue on_failure must be register_clone_unconfirmed, "
        f"got: {step.on_failure!r}"
    )


# ---------------------------------------------------------------------------
# T9 family-specific: fallback, on_failure, reenter_merge_queue_cheap
# (register_clone_unconfirmed family only)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("recipe_fixture", RELEASE_TIMEOUT_RECIPES)
def test_wait_for_queue_fallback_routes_to_register_clone_unconfirmed(
    recipe_fixture, request
) -> None:
    """wait_for_queue fallback (when=None) must route to register_clone_unconfirmed."""
    recipe = request.getfixturevalue(recipe_fixture)
    step = recipe.steps["wait_for_queue"]
    assert step.on_result is not None
    fallback_conditions = [c for c in step.on_result.conditions if c.when is None]
    assert fallback_conditions, (
        f"{recipe_fixture}: wait_for_queue.on_result must have a fallback condition"
    )
    assert fallback_conditions[0].route == "register_clone_unconfirmed", (
        f"{recipe_fixture}: wait_for_queue fallback must route to register_clone_unconfirmed, "
        f"got: {fallback_conditions[0].route!r}"
    )


@pytest.mark.parametrize("recipe_fixture", RELEASE_TIMEOUT_RECIPES)
def test_wait_for_queue_on_failure_routes_to_register_clone_unconfirmed(
    recipe_fixture, request
) -> None:
    """wait_for_queue.on_failure must route to register_clone_unconfirmed."""
    recipe = request.getfixturevalue(recipe_fixture)
    step = recipe.steps["wait_for_queue"]
    assert step.on_failure == "register_clone_unconfirmed", (
        f"{recipe_fixture}: wait_for_queue on_failure must be register_clone_unconfirmed, "
        f"got: {step.on_failure!r}"
    )


@pytest.mark.parametrize("recipe_fixture", RELEASE_TIMEOUT_RECIPES)
def test_wait_for_queue_dropped_healthy_routes_through_circuit_breaker(
    recipe_fixture, request
) -> None:
    """dropped_healthy must route to check_dropped_healthy_loop circuit breaker,
    which in turn routes to reenter_merge_queue_cheap on DROPPED_OK."""
    recipe = request.getfixturevalue(recipe_fixture)
    assert "reenter_merge_queue_cheap" in recipe.steps, (
        f"{recipe_fixture}: reenter_merge_queue_cheap step must exist"
    )
    assert "check_dropped_healthy_loop" in recipe.steps, (
        f"{recipe_fixture}: check_dropped_healthy_loop step must exist"
    )
    step = recipe.steps["wait_for_queue"]
    assert step.on_result is not None
    dropped_routes = [
        c
        for c in step.on_result.conditions
        if c.when is not None
        and "dropped_healthy" in c.when
        and c.route == "check_dropped_healthy_loop"
    ]
    assert dropped_routes, (
        f"{recipe_fixture}: dropped_healthy must route to check_dropped_healthy_loop"
    )
    cb_step = recipe.steps["check_dropped_healthy_loop"]
    assert cb_step.on_result is not None
    ok_routes = [c for c in cb_step.on_result.conditions if c.when is None]
    assert ok_routes and ok_routes[0].route == "reenter_merge_queue_cheap", (
        f"{recipe_fixture}: check_dropped_healthy_loop fallthrough must route to"
        " reenter_merge_queue_cheap"
    )


# ---------------------------------------------------------------------------
# ci_watch_post_queue_fix bounded loops
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("recipe_fixture", RELEASE_TIMEOUT_RECIPES)
def test_ci_watch_post_queue_fix_no_runs_routes_to_failure(recipe_fixture, request):
    """ci_watch_post_queue_fix no_runs must route to release_issue_failure."""
    recipe = request.getfixturevalue(recipe_fixture)
    step = recipe.steps["ci_watch_post_queue_fix"]
    no_runs_routes = [
        c.route for c in step.on_result.conditions if c.when and "'no_runs'" in c.when
    ]
    assert "release_issue_failure" in no_runs_routes, (
        f"{recipe_fixture}: ci_watch_post_queue_fix no_runs must route to release_issue_failure"
    )


@pytest.mark.parametrize("recipe_fixture", RELEASE_TIMEOUT_RECIPES)
def test_ci_watch_post_queue_fix_timed_out_is_bounded(recipe_fixture, request):
    """ci_watch_post_queue_fix timed_out must route through check_ci_post_queue_loop."""
    recipe = request.getfixturevalue(recipe_fixture)
    step = recipe.steps["ci_watch_post_queue_fix"]
    timed_out_routes = [
        c.route for c in step.on_result.conditions if c.when and "'timed_out'" in c.when
    ]
    assert timed_out_routes, f"{recipe_fixture}: missing timed_out routing"
    assert timed_out_routes[0] == "check_ci_post_queue_loop", (
        f"{recipe_fixture}: timed_out must route through check_ci_post_queue_loop, "
        f"not {timed_out_routes[0]}"
    )


@pytest.mark.parametrize("recipe_fixture", RELEASE_TIMEOUT_RECIPES)
def test_check_ci_post_queue_loop_exists_and_bounded(recipe_fixture, request):
    """check_ci_post_queue_loop step must exist and be bounded."""
    recipe = request.getfixturevalue(recipe_fixture)
    assert "check_ci_post_queue_loop" in recipe.steps, (
        f"{recipe_fixture}: check_ci_post_queue_loop step must exist"
    )
    step = recipe.steps["check_ci_post_queue_loop"]
    assert step.with_args.get("callable") == "autoskillit.smoke_utils.check_loop_iteration"
    assert int(step.with_args.get("max_iterations", 0)) >= 2
