"""Queue mode structural assertions for merge-prs.yaml."""

from __future__ import annotations

import pytest

from autoskillit.recipe.io import builtin_recipes_dir, load_recipe
from autoskillit.recipe.validator import validate_recipe

pytestmark = [pytest.mark.layer("recipe"), pytest.mark.small]

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def pmp_recipe():
    return load_recipe(builtin_recipes_dir() / "merge-prs.yaml")


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
    """enqueue_current_pr step must exist with tool=enqueue_pr."""
    assert "enqueue_current_pr" in pmp_recipe.steps
    step = pmp_recipe.steps["enqueue_current_pr"]
    assert step.tool == "enqueue_pr"


def test_merge_prs_wait_queue_pr_exists(pmp_recipe) -> None:
    """wait_queue_pr step must exist with tool=wait_for_merge_queue."""
    assert "wait_queue_pr" in pmp_recipe.steps
    step = pmp_recipe.steps["wait_queue_pr"]
    assert step.tool == "wait_for_merge_queue"


def test_merge_prs_reenter_queue_exists(pmp_recipe) -> None:
    """reenter_queue step must exist."""
    assert "reenter_queue" in pmp_recipe.steps


def test_merge_prs_advance_queue_pr_exists(pmp_recipe) -> None:
    """advance_queue_pr step must exist with tool=run_python."""
    assert "advance_queue_pr" in pmp_recipe.steps
    step = pmp_recipe.steps["advance_queue_pr"]
    assert step.tool == "run_python"


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


def test_merge_prs_classic_path_create_batch_branch_present(pmp_recipe) -> None:
    """create_batch_branch step must still be present (classic path)."""
    assert "create_batch_branch" in pmp_recipe.steps


def test_merge_prs_classic_path_merge_pr_present(pmp_recipe) -> None:
    """merge_pr step must still be present (classic path)."""
    assert "merge_pr" in pmp_recipe.steps


def test_merge_prs_classic_path_push_batch_branch_present(pmp_recipe) -> None:
    """push_batch_branch step must still be present (classic path)."""
    assert "push_batch_branch" in pmp_recipe.steps


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
    """enqueue_current_pr must reference context.current_pr_number (no batch loop)."""
    step = pmp_recipe.steps["enqueue_current_pr"]
    pr_number = step.with_args.get("pr_number", "")
    assert "context.current_pr_number" in pr_number


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
    """attempt_cheap_rebase step must exist with tool=run_python."""
    assert "attempt_cheap_rebase" in pmp_recipe.steps
    step = pmp_recipe.steps["attempt_cheap_rebase"]
    assert step.tool == "run_python"
    assert step.with_args.get("callable") == "autoskillit.recipe._cmd_rpc.attempt_cheap_rebase"


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
# merge-prs.yaml — Proactive rebase block (Test 1E)
# ---------------------------------------------------------------------------


def test_merge_prs_advance_queue_pr_fallthrough_routes_to_get_next_pr_branch(
    pmp_recipe,
) -> None:
    step = pmp_recipe.steps["advance_queue_pr"]
    assert step.on_result is not None
    fallback_route = next(
        (c.route for c in step.on_result.conditions if c.when is None),
        None,
    )
    assert fallback_route == "get_next_pr_branch"


def test_merge_prs_get_next_pr_branch_exists(pmp_recipe) -> None:
    assert "get_next_pr_branch" in pmp_recipe.steps
    assert pmp_recipe.steps["get_next_pr_branch"].tool == "run_cmd"


def test_merge_prs_get_next_pr_branch_cmd_fetches_headref(pmp_recipe) -> None:
    cmd = pmp_recipe.steps["get_next_pr_branch"].with_args.get("cmd", "")
    assert "gh pr view" in cmd
    assert "headRefName" in cmd


def test_merge_prs_get_next_pr_branch_captures_next_pr_branch(pmp_recipe) -> None:
    step = pmp_recipe.steps["get_next_pr_branch"]
    assert "next_pr_branch" in (step.capture or {})


def test_merge_prs_get_next_pr_branch_on_success_routes_to_proactive_rebase(
    pmp_recipe,
) -> None:
    assert pmp_recipe.steps["get_next_pr_branch"].on_success == "proactive_rebase_next_pr"


def test_merge_prs_get_next_pr_branch_on_failure_routes_to_enqueue(pmp_recipe) -> None:
    # Safety net: if gh pr view fails, fall through to enqueue as if no rebase happened
    assert pmp_recipe.steps["get_next_pr_branch"].on_failure == "enqueue_current_pr"


def test_merge_prs_proactive_rebase_next_pr_exists(pmp_recipe) -> None:
    assert "proactive_rebase_next_pr" in pmp_recipe.steps
    step = pmp_recipe.steps["proactive_rebase_next_pr"]
    assert step.tool == "run_python"
    assert step.with_args.get("callable") == "autoskillit.recipe._cmd_rpc.proactive_rebase_next_pr"


def test_merge_prs_proactive_rebase_next_pr_routing(pmp_recipe) -> None:
    step = pmp_recipe.steps["proactive_rebase_next_pr"]
    assert step.on_result is not None
    clean_route = next(
        (c.route for c in step.on_result.conditions if c.when and "clean" in c.when),
        None,
    )
    assert clean_route == "push_rebased_next_pr"
    fallback_route = next(
        (c.route for c in step.on_result.conditions if c.when is None),
        None,
    )
    assert fallback_route == "resolve_proactive_rebase_conflicts"


def test_merge_prs_proactive_rebase_next_pr_on_failure_routes_to_enqueue(pmp_recipe) -> None:
    # Safety net: git command errors fall through to enqueue (don't block the pipeline)
    assert pmp_recipe.steps["proactive_rebase_next_pr"].on_failure == "enqueue_current_pr"


def test_merge_prs_push_rebased_next_pr_exists(pmp_recipe) -> None:
    step = pmp_recipe.steps["push_rebased_next_pr"]
    assert step.tool == "push_to_remote"
    assert step.with_args.get("force") == "true"


def test_merge_prs_push_rebased_next_pr_routes_to_enqueue(pmp_recipe) -> None:
    assert pmp_recipe.steps["push_rebased_next_pr"].on_success == "enqueue_current_pr"


def test_merge_prs_push_rebased_next_pr_on_failure_routes_to_register_failure(
    pmp_recipe,
) -> None:
    assert pmp_recipe.steps["push_rebased_next_pr"].on_failure == "register_clone_failure"


def test_merge_prs_resolve_proactive_conflicts_exists(pmp_recipe) -> None:
    step = pmp_recipe.steps["resolve_proactive_rebase_conflicts"]
    assert step.tool == "run_skill"
    assert "resolve-merge-conflicts" in step.with_args.get("skill_command", "")


def test_merge_prs_resolve_proactive_conflicts_routing(pmp_recipe) -> None:
    step = pmp_recipe.steps["resolve_proactive_rebase_conflicts"]
    assert step.on_result is not None
    escalation_route = next(
        (c.route for c in step.on_result.conditions if c.when and "escalation_required" in c.when),
        None,
    )
    assert escalation_route == "register_clone_failure"
    success_route = next(
        (c.route for c in step.on_result.conditions if c.when is None),
        None,
    )
    assert success_route == "push_rebased_next_pr"


def test_merge_prs_resolve_proactive_conflicts_has_retries(pmp_recipe) -> None:
    step = pmp_recipe.steps["resolve_proactive_rebase_conflicts"]
    assert step.retries == 1
    assert step.on_exhausted == "enqueue_current_pr"


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
    """ci_watch_post_queue_fix on_result success must route to reenter_queue."""
    step = pmp_recipe.steps["ci_watch_post_queue_fix"]
    assert step.on_result is not None, "ci_watch_post_queue_fix must use on_result routing"
    success_routes = [
        c.route for c in step.on_result.conditions if c.when and "'success'" in c.when
    ]
    assert "reenter_queue" in success_routes


# ---------------------------------------------------------------------------
# merge-prs.yaml — recipe-level capture for advancement (Test 1E)
# ---------------------------------------------------------------------------


def test_merge_prs_advance_queue_pr_is_run_python(pmp_recipe) -> None:
    """advance_queue_pr step must exist with tool=run_python."""
    assert "advance_queue_pr" in pmp_recipe.steps
    step = pmp_recipe.steps["advance_queue_pr"]
    assert step.tool == "run_python"


def test_merge_prs_next_queue_pr_or_done_removed(pmp_recipe) -> None:
    """next_queue_pr_or_done step must be removed (replaced by advance_queue_pr)."""
    assert "next_queue_pr_or_done" not in pmp_recipe.steps


def test_merge_prs_advance_queue_pr_callable_references_pr_order(pmp_recipe) -> None:
    """advance_queue_pr callable args must reference pr_order_file and current_pr_number."""
    step = pmp_recipe.steps["advance_queue_pr"]
    args = step.with_args
    assert "pr_order_file" in args
    assert "current_pr_number" in args


def test_merge_prs_advance_queue_pr_captures_pr_number(pmp_recipe) -> None:
    """advance_queue_pr must have a capture block for current_pr_number."""
    step = pmp_recipe.steps["advance_queue_pr"]
    capture = step.capture or {}
    assert "current_pr_number" in capture


def test_merge_prs_advance_queue_pr_routing(pmp_recipe) -> None:
    """advance_queue_pr routes to get_next_pr_branch (default) or collect_and_check_impl_plans."""
    step = pmp_recipe.steps["advance_queue_pr"]
    assert step.on_result is not None
    conditions = step.on_result.conditions
    done_routes = [c for c in conditions if c.when and "done" in c.when]
    assert done_routes, "must have a 'done' condition"
    assert done_routes[0].route == "collect_and_check_impl_plans"
    default_routes = [c for c in conditions if c.when is None]
    assert default_routes, "must have a default route"
    assert default_routes[0].route == "get_next_pr_branch"


# ---------------------------------------------------------------------------
# merge-prs.yaml — new PRState route steps (Test 1F)
# ---------------------------------------------------------------------------


def test_merge_prs_diagnose_queue_ci_exists(pmp_recipe) -> None:
    """diagnose_queue_ci step must exist with tool=run_skill."""
    assert "diagnose_queue_ci" in pmp_recipe.steps
    step = pmp_recipe.steps["diagnose_queue_ci"]
    assert step.tool == "run_skill"


def test_merge_prs_reenroll_stalled_queue_pr_exists(pmp_recipe) -> None:
    """reenroll_stalled_queue_pr step must exist with tool=enqueue_pr."""
    assert "reenroll_stalled_queue_pr" in pmp_recipe.steps
    step = pmp_recipe.steps["reenroll_stalled_queue_pr"]
    assert step.tool == "enqueue_pr"


def test_merge_prs_reenroll_stalled_routes_to_wait(pmp_recipe) -> None:
    """reenroll_stalled_queue_pr must route through the stall loop guard."""
    step = pmp_recipe.steps["reenroll_stalled_queue_pr"]
    assert step.on_success == "check_queue_stall_loop"


def test_merge_prs_dropped_healthy_routes_through_circuit_breaker(pmp_recipe) -> None:
    """dropped_healthy in wait_queue_pr must route to check_dropped_healthy_loop,
    which in turn routes to reenter_queue on DROPPED_OK."""
    assert "check_dropped_healthy_loop" in pmp_recipe.steps, (
        "check_dropped_healthy_loop step must exist in merge-prs recipe"
    )
    step = pmp_recipe.steps["wait_queue_pr"]
    assert step.on_result is not None
    dropped_routes = [
        c
        for c in step.on_result.conditions
        if c.when is not None
        and "dropped_healthy" in c.when
        and c.route == "check_dropped_healthy_loop"
    ]
    assert dropped_routes, "dropped_healthy must route to check_dropped_healthy_loop"
    cb_step = pmp_recipe.steps["check_dropped_healthy_loop"]
    assert cb_step.on_result is not None
    ok_routes = [c for c in cb_step.on_result.conditions if c.when is None]
    assert ok_routes and ok_routes[0].route == "reenter_queue", (
        "check_dropped_healthy_loop fallthrough must route to reenter_queue"
    )


# ---------------------------------------------------------------------------
# merge-prs.yaml — Part B: expanded captures + unified enrollment
# ---------------------------------------------------------------------------


def test_merge_prs_check_repo_ci_event_captures_auto_merge_available(pmp_recipe) -> None:
    """check_repo_ci_event must capture auto_merge_available (expanded captures)."""
    step = pmp_recipe.steps["check_repo_ci_event"]
    assert step.tool == "check_repo_merge_state"
    assert "auto_merge_available" in (step.capture or {})


def test_merge_prs_enqueue_current_pr_uses_enqueue_pr_tool(pmp_recipe) -> None:
    """enqueue_current_pr must use enqueue_pr tool."""
    step = pmp_recipe.steps["enqueue_current_pr"]
    assert step.tool == "enqueue_pr"


def test_merge_prs_reenter_queue_uses_enqueue_pr_tool(pmp_recipe) -> None:
    """reenter_queue must use enqueue_pr tool."""
    step = pmp_recipe.steps["reenter_queue"]
    assert step.tool == "enqueue_pr"


def test_merge_prs_reenroll_stalled_queue_pr_uses_enqueue_pr_tool(pmp_recipe) -> None:
    """reenroll_stalled_queue_pr must use enqueue_pr tool."""
    step = pmp_recipe.steps["reenroll_stalled_queue_pr"]
    assert step.tool == "enqueue_pr"


# ---------------------------------------------------------------------------
# NOT_ENROLLED routing — merge-prs.yaml only
# ---------------------------------------------------------------------------


def test_merge_prs_wait_queue_pr_routes_not_enrolled(pmp_recipe) -> None:
    """wait_queue_pr must have an explicit routing arm for not_enrolled."""
    step = pmp_recipe.steps["wait_queue_pr"]
    conditions = [c.when for c in step.on_result.conditions]
    assert any("not_enrolled" in c for c in conditions if c)


# ---------------------------------------------------------------------------
# merge-prs.yaml — hardcoded-origin semantic rule
# ---------------------------------------------------------------------------


def test_no_hardcoded_origin_in_run_cmd_merge_prs(pmp_recipe) -> None:
    """merge-prs.yaml setup_remote suppresses hardcoded-origin-in-run-cmd recipe-wide."""
    from autoskillit.recipe.validator import run_semantic_rules

    findings = run_semantic_rules(pmp_recipe)
    violations = [f for f in findings if f.rule == "hardcoded-origin-in-run-cmd"]
    assert violations == [], (
        f"hardcoded-origin-in-run-cmd fired on merge-prs.yaml: {[v.step_name for v in violations]}"
    )


# ---------------------------------------------------------------------------
# merge-prs.yaml — check_eject_limit
# ---------------------------------------------------------------------------


def test_check_eject_limit_step_exists_in_merge_prs(pmp_recipe) -> None:
    """check_eject_limit step must exist in merge-prs.yaml."""
    assert "check_eject_limit" in pmp_recipe.steps


def test_check_eject_limit_routes_to_get_ejected_pr_branch_in_merge_prs(pmp_recipe) -> None:
    """check_eject_limit default route in merge-prs.yaml must point to get_ejected_pr_branch."""
    step = pmp_recipe.steps["check_eject_limit"]
    assert step.on_result is not None
    conds = step.on_result.conditions
    default_conds = [c for c in conds if c.when is None]
    assert len(default_conds) == 1
    assert default_conds[0].route == "get_ejected_pr_branch"


def test_check_eject_limit_routes_to_register_clone_failure_in_merge_prs(pmp_recipe) -> None:
    """check_eject_limit in merge-prs.yaml must route to register_clone_failure on limit."""
    step = pmp_recipe.steps["check_eject_limit"]
    assert step.on_result is not None
    conds = step.on_result.conditions
    limit_conds = [c for c in conds if c.when and "EJECT_LIMIT_EXCEEDED" in c.when]
    assert len(limit_conds) == 1
    assert limit_conds[0].route == "register_clone_failure"


def test_merge_prs_ci_watch_post_queue_fix_no_runs_routes_to_failure(pmp_recipe) -> None:
    """ci_watch_post_queue_fix no_runs must route to register_clone_failure in merge-prs."""
    step = pmp_recipe.steps["ci_watch_post_queue_fix"]
    no_runs_routes = [
        c.route for c in step.on_result.conditions if c.when and "'no_runs'" in c.when
    ]
    assert "register_clone_failure" in no_runs_routes


def test_merge_prs_ci_watch_post_queue_fix_uses_branch_param(pmp_recipe) -> None:
    """ci_watch_post_queue_fix must use 'branch' parameter, not 'pr_number'."""
    step = pmp_recipe.steps["ci_watch_post_queue_fix"]
    assert "branch" in step.with_args, (
        "ci_watch_post_queue_fix must use 'branch' parameter, not 'pr_number'"
    )
    assert "pr_number" not in step.with_args, (
        "ci_watch_post_queue_fix must not pass pr_number to wait_for_ci"
    )


def test_merge_prs_ci_watch_post_queue_fix_timed_out_bounded(pmp_recipe) -> None:
    """ci_watch_post_queue_fix timed_out must route through check_ci_post_queue_loop."""
    step = pmp_recipe.steps["ci_watch_post_queue_fix"]
    timed_out_routes = [
        c.route for c in step.on_result.conditions if c.when and "'timed_out'" in c.when
    ]
    assert timed_out_routes, "ci_watch_post_queue_fix: missing timed_out routing"
    assert timed_out_routes[0] == "check_ci_post_queue_loop"
