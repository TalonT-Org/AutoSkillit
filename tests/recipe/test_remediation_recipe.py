"""Structural tests for remediation.yaml recipe."""

from pathlib import Path

import pytest

from autoskillit.recipe.analysis._analysis import make_validation_context
from autoskillit.recipe.analysis._analysis_bfs import (
    _build_success_step_graph,
    all_paths_cross,
    bfs_reachable,
    bfs_reachable_without_barrier,
)
from autoskillit.recipe.io import load_recipe
from autoskillit.recipe.validator import validate_recipe_structure
from tests.recipe.test_implementation import _assert_delivery_projection_contract

pytestmark = [pytest.mark.layer("recipe"), pytest.mark.small]

RECIPE_PATH = (
    Path(__file__).parent.parent.parent / "src" / "autoskillit" / "recipes" / "remediation.yaml"
)
_PRE_DELIVERY_STRUCTURE_SHA256 = (
    "sha256:2c1ed9c73e0508a78416c1138ca97ef5c3ff6e2ff3077deb5c38a005d04d5df7"
)


@pytest.fixture(scope="module")
def recipe():
    return load_recipe(RECIPE_PATH)


def test_delivery_declarations_preserve_canonical_recipe_structure(recipe) -> None:
    _assert_delivery_projection_contract(recipe, RECIPE_PATH, _PRE_DELIVERY_STRUCTURE_SHA256)


def test_context_limited_walkthrough_renews_before_retry(recipe) -> None:
    assert recipe.steps["dry_walkthrough"].on_context_limit == "renew_before_retry"
    renewal = recipe.steps["renew_before_retry"]
    assert renewal.tool == "bind_plan_set"
    assert renewal.on_result is not None
    assert any(c.route == "retry_walkthrough" for c in renewal.on_result.conditions)
    for key in ("plan_set_authority_path", "plan_set_authority_digest", "plan_set_parts"):
        assert key in renewal.capture


def test_coverage_replan_is_bounded(recipe) -> None:
    bind = recipe.steps["bind_plan_set"]
    assert bind.on_result is not None
    assert any(c.route == "check_replan_iteration" for c in bind.on_result.conditions)
    guard = recipe.steps["check_replan_iteration"]
    assert guard.with_args["max_iterations"] == "2"
    assert guard.on_result is not None
    assert any(
        c.route == "release_issue_failure" and c.when and "max_exceeded" in c.when
        for c in guard.on_result.conditions
    )


def test_remediation_recipe_has_release_issue_success_step(recipe):
    """remediation.yaml must have a release_issue step on the success path.

    Absence of this step means issues resolved via remediation never get the
    staged label applied.
    """
    errors = validate_recipe_structure(recipe)
    assert not errors, f"remediation.yaml failed schema validation: {errors}"
    step_names = list(recipe.steps.keys())
    assert any("release_issue" in name and "success" in name for name in step_names), (
        "remediation.yaml is missing a release_issue step on the success path. "
        "Without it, issues are never promoted to staged state after a successful remediation."
    )


# T_REM_LOOP1
def test_check_review_loop_step_exists(recipe) -> None:
    """check_review_loop step must exist in remediation recipe."""
    assert "check_review_loop" in recipe.steps


# T_REM_LOOP2
def test_pre_review_rebase_routes_to_re_push_review(recipe) -> None:
    """pre_review_rebase uses run_python and routes clean to re_push_review."""
    assert "pre_review_rebase" in recipe.steps
    step = recipe.steps["pre_review_rebase"]
    assert step.tool == "run_python"
    assert step.on_success is None, "routing is via on_result, not on_success"
    assert step.on_result is not None
    clean_routes = [c.route for c in step.on_result.conditions if c.when and "clean" in c.when]
    assert "re_push_review" in clean_routes
    assert step.on_failure == "resolve_pre_review_conflicts"


def test_re_push_review_routes_to_check_review_posted(recipe) -> None:
    """re_push_review on_success must route to check_review_posted (effect verification gate)."""
    step = recipe.steps["re_push_review"]
    assert step.on_success == "check_review_posted"


def test_pre_remediation_merge_routes_path_validation_to_remediate(recipe) -> None:
    """path_validation on pre_remediation_merge must route to remediate, not fall
    through to result.error — a missing worktree on resume means the prior session
    already merged it."""
    step = recipe.steps["pre_remediation_merge"]
    assert step.on_result is not None
    pv_routes = [
        c.route for c in step.on_result.conditions if c.when and "path_validation" in c.when
    ]
    assert pv_routes, (
        "pre_remediation_merge must explicitly route path_validation; "
        "without it, a missing worktree on resume falls through to "
        "release_issue_failure"
    )
    assert pv_routes[0] == "remediate"


def _routes(step):
    assert step.on_result is not None
    return {condition.when: condition for condition in step.on_result.conditions}


def _early_recovery_steps(recipe):
    return bfs_reachable_without_barrier(
        recipe,
        "check_merge_fix_loop_pre_remediation",
        frozenset({"pre_remediation_merge", "release_issue_failure"}),
    )


def test_pre_remediation_merge_routes_both_test_gates_to_bounded_recovery(recipe) -> None:
    routes = _routes(recipe.steps["pre_remediation_merge"])
    for failed_step in ("test_gate", "post_rebase_test_gate"):
        assert routes[f"result.failed_step == '{failed_step}'"].route == (
            "check_merge_fix_loop_pre_remediation"
        )

    guard = recipe.steps["check_merge_fix_loop_pre_remediation"]
    assert guard.tool == "run_python"
    assert guard.with_args["callable"] == "autoskillit.smoke_utils.check_loop_iteration"
    guard_routes = _routes(guard)
    assert next(c.route for c in guard_routes.values() if c.when and "max_exceeded" in c.when) == (
        "release_issue_failure"
    )
    assert guard_routes[None].route == "diagnose_merge_gate_pre_remediation"

    reached = _early_recovery_steps(recipe)
    assert any(
        recipe.steps[name].tool == "run_skill"
        and "resolve-failures" in recipe.steps[name].with_args.get("skill_command", "")
        for name in reached
    )
    assert any(recipe.steps[name].tool == "test_check" for name in reached)
    assert not reached.intersection(
        {
            "implement",
            "retry_worktree",
            "remediate",
            "merge",
            "check_merge_fix_loop",
            "diagnose_merge_gate",
            "merge_gate_assess",
            "merge_gate_test",
            "check_test_fix_loop",
            "assess",
            "test",
            "audit_impl",
        }
    )


def test_pre_remediation_recovery_returns_to_same_merge_site_with_same_refs(recipe) -> None:
    assert recipe.steps["merge_gate_test_pre_remediation"].on_success == (
        "commit_guard_pre_remediation"
    )
    assert _routes(recipe.steps["commit_guard_pre_remediation"])[None].route == (
        "pre_remediation_merge"
    )
    merge = recipe.steps["pre_remediation_merge"]
    assert merge.with_args["worktree_path"] == "${{ context.implementation_ref }}"
    assert merge.with_args["base_branch"] == "${{ context.merge_target }}"
    assert merge.capture["implementation_ref"].from_ == "${{ result.worktree_path }}"
    for name in (_early_recovery_steps(recipe) - {"pre_remediation_merge"}) | {
        "commit_guard_pre_remediation"
    }:
        assert not set(recipe.steps[name].capture).intersection(
            {"implementation_ref", "merge_target", "worktree_path"}
        )
    assess = recipe.steps["merge_gate_assess_pre_remediation"]
    assert assess.with_args["skill_inputs"]["worktree_path"] == (
        "${{ context.implementation_ref }}"
    )
    assert assess.with_args["cwd"] == "${{ context.implementation_ref }}"
    assert recipe.steps["merge_gate_test_pre_remediation"].with_args["worktree_path"] == (
        "${{ context.implementation_ref }}"
    )
    assert {name for name, step in recipe.steps.items() if "merge_target" in step.capture} == {
        "clone",
        "create_and_publish",
    }


def test_early_and_late_merge_gate_continuations_are_independent(recipe) -> None:
    late = recipe.steps["merge_gate_test"]
    assert late.on_success == "audit_impl"
    assert late.on_failure == "check_merge_test_fix_loop"
    assert recipe.steps["merge_gate_test_pre_remediation"].on_failure == (
        "check_merge_fix_loop_pre_remediation"
    )
    assert "merge_gate_test" not in _early_recovery_steps(recipe)


def test_pre_remediation_test_gate_contention_stays_terminal(recipe) -> None:
    assert (
        _routes(recipe.steps["pre_remediation_merge"])[
            "result.failed_step == 'test_gate_contention'"
        ].route
        == "release_issue_failure"
    )
    assert "check_merge_fix_loop_pre_remediation" not in bfs_reachable(
        make_validation_context(recipe).step_graph, "release_issue_failure"
    )


def test_pre_remediation_merge_fix_budget_is_reset_per_audit_cycle_only(recipe) -> None:
    guard = recipe.steps["check_merge_fix_loop_pre_remediation"]
    assert guard.with_args["current_iteration"] == (
        "${{ context.pre_remediation_merge_fix_count }}"
    )
    assert guard.with_args["max_iterations"] == "${{ inputs.merge_fix_max_retries }}"
    assert "pre_remediation_merge_fix_count" in guard.capture
    reset = recipe.steps["reset_pre_remediation_merge_fix_counter"]
    assert reset.tool == "run_python"
    assert reset.with_args["callable"] == "autoskillit.smoke_utils.init_counter"
    assert "pre_remediation_merge_fix_count" in reset.capture
    assert all_paths_cross(
        _build_success_step_graph(recipe),
        "merge_audit_cycle_path",
        "reset_pre_remediation_merge_fix_counter",
        "check_merge_fix_loop_pre_remediation",
    )
    assert {
        name
        for name in _early_recovery_steps(recipe)
        if "pre_remediation_merge_fix_count" in recipe.steps[name].capture
    } == {"check_merge_fix_loop_pre_remediation"}


def test_pre_remediation_merge_exhaustion_preserves_worktree(recipe) -> None:
    guard = recipe.steps["check_merge_fix_loop_pre_remediation"]
    assert next(
        c.route for c in _routes(guard).values() if c.when and "max_exceeded" in c.when
    ) == ("release_issue_failure")
    reached = bfs_reachable(make_validation_context(recipe).step_graph, "release_issue_failure")
    assert all(
        recipe.steps[name].tool not in {"merge_worktree", "remove_clone", "reset_workspace"}
        for name in reached
    )


def test_remediation_pre_remediation_waivers_are_exactly_declared(recipe) -> None:
    early = _routes(recipe.steps["pre_remediation_merge"])
    waived = {
        when.split("'")[1]
        for when, condition in early.items()
        if when and condition.recovery_waiver
    }
    assert waived == {"test_gate_contention", "rebase", "dirty_tree"}
    assert all(c.recovery_waiver is None for c in _routes(recipe.steps["merge"]).values())
    for failed_step in ("test_gate", "post_rebase_test_gate"):
        assert early[f"result.failed_step == '{failed_step}'"].recovery_waiver is None


# T_REM_LOOP3
def test_check_review_loop_routes_to_annotate_pr_diff_when_had_blocking(
    recipe,
) -> None:
    """check_review_loop on_result condition must gate on had_blocking AND max_exceeded."""
    step = recipe.steps["check_review_loop"]
    assert step.on_result is not None
    review_conditions = [
        c
        for c in step.on_result.conditions
        if c.when is not None and c.route == "pre_review_cleanup"
    ]
    assert review_conditions, "No conditional route to pre_review_cleanup found"
    cond = review_conditions[0].when
    assert "had_blocking" in cond
    assert "max_exceeded" in cond


# T_REM_LOOP4
def test_check_review_loop_has_on_failure(recipe) -> None:
    """check_review_loop must declare on_failure because it uses on_result
    (on-result-missing-failure-route semantic rule requires it)."""
    step = recipe.steps["check_review_loop"]
    assert step.on_failure == "check_repo_ci_event"


# T_REM_LOOP5
def test_review_pr_routes_approved_with_comments_to_resolve_review(recipe) -> None:
    """review_pr on_result must route approved_with_comments to resolve_review."""
    step = recipe.steps["review_pr"]
    assert step.on_result is not None
    routes = {c.when: c.route for c in step.on_result.conditions if c.when}
    matching = [
        when
        for when, route in routes.items()
        if "approved_with_comments" in when and route == "resolve_review"
    ]
    assert matching, "No approved_with_comments → resolve_review route found"


# T_REM_LOOP6
def test_review_pr_captures_review_verdict(recipe) -> None:
    """review_pr must capture verdict as review_verdict (not verdict) to avoid clobber."""
    step = recipe.steps["review_pr"]
    capture = step.capture or {}
    assert "review_verdict" in capture
    assert "result.verdict" in capture["review_verdict"].from_


# T_REM_LOOP7
def test_check_review_loop_with_args_has_previous_verdict(recipe) -> None:
    """check_review_loop with: must pass previous_verdict from context.review_verdict."""
    step = recipe.steps["check_review_loop"]
    assert "previous_verdict" in step.with_args
    assert "review_verdict" in step.with_args["previous_verdict"]


def test_ci_watch_timed_out_routes_to_guard(recipe) -> None:
    """ci_watch timed_out route goes to check_ci_timed_out_loop, not self."""
    step = recipe.steps["ci_watch"]
    assert step.on_result is not None
    timed_out_conds = [c for c in step.on_result.conditions if c.when and "timed_out" in c.when]
    assert timed_out_conds, "ci_watch must have a timed_out condition"
    assert timed_out_conds[0].route == "check_ci_timed_out_loop", (
        "ci_watch timed_out must route to check_ci_timed_out_loop, not self"
    )


def test_check_ci_timed_out_loop_exists_with_correct_pattern(recipe) -> None:
    """check_ci_timed_out_loop uses check_loop_iteration with max_iterations: 2."""
    assert "check_ci_timed_out_loop" in recipe.steps
    step = recipe.steps["check_ci_timed_out_loop"]
    assert step.tool == "run_python"
    assert "check_loop_iteration" in step.with_args.get("callable", "")
    assert step.with_args.get("max_iterations") == "2"
    assert step.on_result is not None
    max_exceeded_conds = [
        c
        for c in step.on_result.conditions
        if c.when and "max_exceeded" in c.when and "true" in c.when
    ]
    assert max_exceeded_conds, "check_ci_timed_out_loop must route max_exceeded==true"
    assert max_exceeded_conds[0].route == "detect_ci_conflict"


def test_remediation_next_or_done_step_exists(recipe) -> None:
    """T_REM_MP1: remediation.yaml must have a next_or_done routing step."""
    assert "next_or_done" in recipe.steps
    step = recipe.steps["next_or_done"]
    assert step.action == "route"


def test_remediation_next_or_done_routes_more_parts_to_dry_walkthrough(recipe) -> None:
    """T_REM_MP2: next_or_done must route more_parts back to dry_walkthrough."""
    step = recipe.steps["next_or_done"]
    assert step.on_result is not None
    conds = step.on_result.conditions
    assert any(
        c.route == "dry_walkthrough" and c.when is not None and "more_parts" in c.when
        for c in conds
    ), "next_or_done must have a predicate routing more_parts → dry_walkthrough"


def test_remediation_next_or_done_routes_done_to_push(recipe) -> None:
    """T_REM_MP3: next_or_done fallthrough must route to check_has_commits (all parts complete)."""
    step = recipe.steps["next_or_done"]
    assert step.on_result is not None
    conds = step.on_result.conditions
    assert any(c.route == "check_has_commits" for c in conds), (
        "next_or_done must have a fallthrough condition routing to check_has_commits"
    )


def test_remediation_merge_routes_to_inter_part_push(recipe) -> None:
    """T_REM_MP4: merge step default route must be inter_part_push, not push or next_or_done."""
    step = recipe.steps["merge"]
    assert step.on_result is not None
    default_routes = [c.route for c in step.on_result.conditions if c.when is None]
    assert default_routes == ["inter_part_push"], (
        f"merge default route must be inter_part_push, got {default_routes}"
    )


def test_remediation_has_no_sprint_mode_ingredient() -> None:
    """remediation.yaml must not declare sprint_mode after sprint-prefix removal."""
    recipe = load_recipe(RECIPE_PATH)
    assert "sprint_mode" not in recipe.ingredients


def test_remediation_has_no_sprint_entry_step() -> None:
    """remediation.yaml must not have a sprint_entry step after sprint-prefix removal."""
    recipe = load_recipe(RECIPE_PATH)
    assert "sprint_entry" not in recipe.steps


def test_remediation_validates_clean_after_sprint_removal() -> None:
    """remediation.yaml must pass schema validation after sprint references removed."""
    recipe = load_recipe(RECIPE_PATH)
    errors = validate_recipe_structure(recipe)
    assert not errors, f"remediation.yaml failed validation after sprint removal: {errors}"


def test_done_unconfirmed_stop_exists(recipe) -> None:
    """remediation.yaml must have a done_unconfirmed stop step for merge-timeout paths."""
    assert "done_unconfirmed" in recipe.steps
    step = recipe.steps["done_unconfirmed"]
    assert step.action == "stop"
    assert len(step.message) >= 10
    assert "unconfirmed" in step.message.lower() or "timeout" in step.message.lower()


def test_done_step_uses_underscore_reason(recipe) -> None:
    """done step must use remediation_complete (underscore convention)."""
    step = recipe.steps["done"]
    assert step.action == "stop"
    assert '"remediation_complete"' in step.message


def test_done_no_changes_stop_exists(recipe) -> None:
    """remediation.yaml must have a done_no_changes stop step."""
    assert "done_no_changes" in recipe.steps
    step = recipe.steps["done_no_changes"]
    assert step.action == "stop"
    assert '"no_changes"' in step.message


def test_done_already_done_stop_exists(recipe) -> None:
    """remediation.yaml must have a done_already_done stop step."""
    assert "done_already_done" in recipe.steps
    step = recipe.steps["done_already_done"]
    assert step.action == "stop"
    assert '"already_done"' in step.message


def test_register_clone_no_changes_routes_to_diagnostic(recipe) -> None:
    """register_clone_no_changes must route to analyze_pipeline_health_no_changes."""
    assert "register_clone_no_changes" in recipe.steps
    step = recipe.steps["register_clone_no_changes"]
    assert step.on_success == "analyze_pipeline_health_no_changes"


def test_register_clone_already_done_routes_to_diagnostic(recipe) -> None:
    """register_clone_already_done must route to analyze_pipeline_health_already_done."""
    assert "register_clone_already_done" in recipe.steps
    step = recipe.steps["register_clone_already_done"]
    assert step.on_success == "analyze_pipeline_health_already_done"


def test_register_clone_unconfirmed_routes_to_done_unconfirmed(recipe) -> None:
    """register_clone_unconfirmed must route toward done_unconfirmed."""
    step = recipe.steps["register_clone_unconfirmed"]
    # May route via optional analyze_pipeline_health_unconfirmed step first
    assert step.on_success in ("done_unconfirmed", "analyze_pipeline_health_unconfirmed"), (
        "register_clone_unconfirmed.on_success must route toward done_unconfirmed, "
        f"got {step.on_success!r}"
    )
    assert step.on_failure in ("done_unconfirmed", "analyze_pipeline_health_unconfirmed"), (
        "register_clone_unconfirmed.on_failure must route toward done_unconfirmed, "
        f"got {step.on_failure!r}"
    )


# T7: investigate step has optional: true and skip_when_false
def test_remediation_investigate_step_is_skippable(recipe) -> None:
    """investigate step must have optional: true and skip_when_false: inputs.investigate."""
    step = recipe.steps["investigate"]
    assert step.optional is True
    assert step.skip_when_false == "inputs.investigate"


# T8: investigate ingredient exists with auto default
def test_remediation_has_investigate_ingredient(recipe) -> None:
    """remediation recipe must have an investigate ingredient with default 'auto'."""
    ing = recipe.ingredients["investigate"]
    assert ing.default == "auto"


# T9: bridge_investigation step exists and routes to rectify
def test_remediation_has_bridge_investigation_step(recipe) -> None:
    """remediation recipe must have a bridge_investigation step that uses run_python."""
    step = recipe.steps["bridge_investigation"]
    assert step.tool == "run_python"
    assert step.on_success == "rectify"


def test_remediation_bridge_investigation_capture_is_path_typed(recipe) -> None:
    """bridge_investigation capture must declare type: path for effective_investigation_path."""
    from autoskillit.core import CaptureEntrySpec

    step = recipe.steps["bridge_investigation"]
    entry = step.capture["effective_investigation_path"]
    assert isinstance(entry, CaptureEntrySpec)
    assert entry.value_type == "path"


def test_remediation_bridge_investigation_uses_extract_investigation_callable(recipe) -> None:
    """bridge_investigation must invoke autoskillit.smoke_utils.extract_investigation."""
    step = recipe.steps["bridge_investigation"]
    callable_name = step.with_args["callable"]
    assert callable_name == "autoskillit.smoke_utils.extract_investigation"


def test_remediation_bridge_investigation_on_failure_halts(recipe) -> None:
    """bridge_investigation must halt the pipeline on failure rather than passing garbage."""
    step = recipe.steps["bridge_investigation"]
    assert step.on_failure == "release_issue_failure"


def test_remediation_investigate_routes_to_bridge(recipe) -> None:
    """investigate step on_success must route to bridge_investigation."""
    step = recipe.steps["investigate"]
    assert step.on_success == "bridge_investigation"


def test_claim_and_resolve_captures_investigation_complete(recipe) -> None:
    """claim_and_resolve must capture investigation_complete from the tool response."""
    step = recipe.steps["claim_and_resolve"]
    assert step.capture is not None
    assert "investigation_complete" in step.capture


# T_DEV_REM1
def test_assess_captures_deviation_manifest_path(recipe) -> None:
    """assess must capture deviation_manifest_path as optional_string."""
    step = recipe.steps["assess"]
    capture = step.capture or {}
    assert "deviation_manifest_path" in capture
    assert capture["deviation_manifest_path"].from_ == "${{ result.deviation_manifest_path }}"
    assert capture["deviation_manifest_path"].value_type == "optional_string"


# T_DEV_REM2
def test_retry_worktree_captures_deviation_manifest_path(recipe) -> None:
    """retry_worktree must capture deviation_manifest_path as optional_string."""
    step = recipe.steps["retry_worktree"]
    capture = step.capture or {}
    assert "deviation_manifest_path" in capture
    assert capture["deviation_manifest_path"].from_ == "${{ result.deviation_manifest_path }}"
    assert capture["deviation_manifest_path"].value_type == "optional_string"


# T_DEV_REM3
def test_audit_impl_forwards_deviation_manifest_path(recipe) -> None:
    """audit_impl must forward deviation_manifest_path kwarg and declare it optional."""
    step = recipe.steps["audit_impl"]
    assert step.with_args["skill_inputs"]["deviation_manifest_path"] == (
        "${{ context.deviation_manifest_path }}"
    )
    assert "deviation_manifest_path" in (step.optional_context_refs or [])


# T_DEV_REM4
def test_merge_gate_assess_captures_deviation_manifest_path(recipe) -> None:
    """merge_gate_assess must capture deviation_manifest_path (flows to audit_impl)."""
    step = recipe.steps["merge_gate_assess"]
    capture = step.capture or {}
    assert "deviation_manifest_path" in capture
    assert capture["deviation_manifest_path"].value_type == "optional_string"
