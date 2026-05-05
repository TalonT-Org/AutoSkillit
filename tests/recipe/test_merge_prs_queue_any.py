"""Queue mode structural assertions for the implementation, remediation, and
implementation-groups recipes (strategy-specific per-recipe checks)."""

from __future__ import annotations

import pytest

from autoskillit.recipe.validator import validate_recipe

pytestmark = [pytest.mark.layer("recipe"), pytest.mark.small]


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


def test_implementation_enqueue_to_queue_is_enqueue_pr(impl_recipe) -> None:
    """enqueue_to_queue must use tool=enqueue_pr."""
    assert "enqueue_to_queue" in impl_recipe.steps
    step = impl_recipe.steps["enqueue_to_queue"]
    assert step.tool == "enqueue_pr"


def test_implementation_wait_for_queue_is_wait_for_merge_queue(impl_recipe) -> None:
    """wait_for_queue must use tool=wait_for_merge_queue."""
    assert "wait_for_queue" in impl_recipe.steps
    step = impl_recipe.steps["wait_for_queue"]
    assert step.tool == "wait_for_merge_queue"


def test_implementation_queue_ejected_fix_exists(impl_recipe) -> None:
    """queue_ejected_fix step must exist."""
    assert "queue_ejected_fix" in impl_recipe.steps


def test_queue_ejected_fix_tool_is_run_python(impl_recipe) -> None:
    step = impl_recipe.steps["queue_ejected_fix"]
    assert step.tool == "run_python", (
        "queue_ejected_fix must be a run_python callable step, not a run_skill or run_cmd"
    )
    assert step.with_args.get("callable") == "autoskillit.recipe._cmd_rpc.queue_ejected_fix"


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
        "enqueue_to_queue",
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
    """ci_watch on_result success must route to check_repo_merge_state."""
    step = impl_recipe.steps["ci_watch"]
    assert step.on_result is not None, "ci_watch must use on_result predicate routing"
    success_routes = [
        c.route for c in step.on_result.conditions if c.when and "'success'" in c.when
    ]
    assert "check_repo_merge_state" in success_routes


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
    """route_queue_mode must NOT route to enqueue_to_queue without checking merge_group_trigger.

    Specifically, the conditions list must not contain a bare 'queue_available == true'
    → enqueue_to_queue without also requiring merge_group_trigger == true.
    """
    step = impl_recipe.steps["route_queue_mode"]
    assert step.action == "route"
    conditions = step.on_result.conditions if step.on_result else []
    queue_conditions = [c for c in conditions if c.route == "enqueue_to_queue"]
    assert len(queue_conditions) == 1, "Exactly one condition must route to enqueue_to_queue"
    cond_when = queue_conditions[0].when or ""
    assert "merge_group_trigger" in cond_when, (
        "The enqueue_to_queue route condition must reference merge_group_trigger "
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


def test_remediation_enqueue_to_queue_is_enqueue_pr(remed_recipe) -> None:
    """enqueue_to_queue must use tool=enqueue_pr."""
    assert "enqueue_to_queue" in remed_recipe.steps
    step = remed_recipe.steps["enqueue_to_queue"]
    assert step.tool == "enqueue_pr"


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
        "enqueue_to_queue",
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
    """ci_watch on_result success must route to check_repo_merge_state."""
    step = remed_recipe.steps["ci_watch"]
    assert step.on_result is not None, "ci_watch must use on_result predicate routing"
    success_routes = [
        c.route for c in step.on_result.conditions if c.when and "'success'" in c.when
    ]
    assert "check_repo_merge_state" in success_routes


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
    """route_queue_mode must NOT route to enqueue_to_queue without checking merge_group_trigger."""
    step = remed_recipe.steps["route_queue_mode"]
    conditions = step.on_result.conditions if step.on_result else []
    queue_conditions = [c for c in conditions if c.route == "enqueue_to_queue"]
    assert len(queue_conditions) == 1
    cond_when = queue_conditions[0].when or ""
    assert "merge_group_trigger" in cond_when


def test_impl_groups_has_check_repo_merge_state(impl_groups_recipe) -> None:
    """check_repo_merge_state step must exist in implementation-groups recipe."""
    assert "check_repo_merge_state" in impl_groups_recipe.steps
    step = impl_groups_recipe.steps["check_repo_merge_state"]
    assert step.block == "pre_queue_gate"


def test_impl_groups_route_queue_mode_requires_merge_group_trigger(impl_groups_recipe) -> None:
    """route_queue_mode must NOT route to enqueue_to_queue without checking merge_group_trigger."""
    step = impl_groups_recipe.steps["route_queue_mode"]
    conditions = step.on_result.conditions if step.on_result else []
    queue_conditions = [c for c in conditions if c.route == "enqueue_to_queue"]
    assert len(queue_conditions) == 1
    cond_when = queue_conditions[0].when or ""
    assert "merge_group_trigger" in cond_when
