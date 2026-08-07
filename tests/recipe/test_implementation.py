"""Tests for review-resolve retry loop steps in implementation.yaml (T_IP_LOOP1–T_IP_LOOP10)."""

from __future__ import annotations

import pytest

from autoskillit.recipe.io import builtin_recipes_dir, load_recipe
from autoskillit.recipe.validator import validate_recipe_structure

pytestmark = [pytest.mark.layer("recipe"), pytest.mark.small]

RECIPE_PATH = builtin_recipes_dir() / "implementation.yaml"


@pytest.fixture(scope="module")
def recipe():
    return load_recipe(RECIPE_PATH)


# T_IP_LOOP1
def test_check_review_loop_step_exists(recipe) -> None:
    """check_review_loop step must exist in implementation recipe."""
    assert "check_review_loop" in recipe.steps


# T_IP_LOOP2
def test_check_review_loop_uses_run_python_with_callable(recipe) -> None:
    """check_review_loop must use run_python tool with the smoke_utils callable."""
    step = recipe.steps["check_review_loop"]
    assert step.tool == "run_python"
    assert step.with_args.get("callable") == "autoskillit.smoke_utils.check_review_loop"


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
    assert step.with_args.get("callable") == "autoskillit.smoke_utils.check_loop_iteration"


# T_IP_LOOP3
def test_check_review_loop_has_no_skip_when_false(recipe) -> None:
    """check_review_loop must NOT have skip_when_false."""
    step = recipe.steps["check_review_loop"]
    assert step.skip_when_false is None


# T_IP_LOOP4
def test_check_review_loop_routes_to_annotate_pr_diff_when_had_blocking(
    recipe,
) -> None:
    """check_review_loop on_result routes to annotate_pr_diff only when
    had_blocking=true AND max_exceeded=false."""
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


# T_IP_LOOP5
def test_check_review_loop_on_result_default_routes_to_ci_watch(recipe) -> None:
    """check_review_loop on_result falls through to check_repo_ci_event when no blocking."""
    step = recipe.steps["check_review_loop"]
    assert step.on_result is not None
    default_conditions = [c for c in step.on_result.conditions if c.when is None]
    assert default_conditions, "No default (when=None) condition found"
    assert default_conditions[0].route == "check_repo_ci_event"


# T_IP_LOOP6
def test_check_review_loop_has_on_failure(recipe) -> None:
    """check_review_loop must declare on_failure because it uses on_result
    (on-result-missing-failure-route semantic rule requires it)."""
    step = recipe.steps["check_review_loop"]
    assert step.on_failure == "check_repo_ci_event"


# T_IP_LOOP7
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


# T_IP_LOOP8
def test_review_max_retries_ingredient_exists_with_default_3(recipe) -> None:
    """review_max_retries ingredient must exist with default='3'."""
    assert "review_max_retries" in recipe.ingredients
    ingredient = recipe.ingredients["review_max_retries"]
    assert ingredient.default == "3"


# T_IP_LOOP9
def test_check_review_loop_has_optional_context_refs_with_review_loop_count(recipe) -> None:
    """check_review_loop must declare review_loop_count in optional_context_refs."""
    step = recipe.steps["check_review_loop"]
    assert "review_loop_count" in (step.optional_context_refs or [])


# T_IP_LOOP10
def test_check_review_loop_captures_review_loop_count(recipe) -> None:
    """check_review_loop must capture review_loop_count from result.next_iteration."""
    step = recipe.steps["check_review_loop"]
    capture = step.capture or {}
    assert "review_loop_count" in capture
    assert "next_iteration" in capture["review_loop_count"].from_


# T_IP_LOOP11
def test_review_pr_routes_approved_with_comments_to_resolve_review(recipe) -> None:
    """approved_with_comments routes through enrich_diff_context to resolve_review."""
    step = recipe.steps["review_pr"]
    assert step.on_result is not None
    routes = {c.when: c.route for c in step.on_result.conditions if c.when}
    matching = [
        when
        for when, route in routes.items()
        if "approved_with_comments" in when and route == "enrich_diff_context"
    ]
    assert matching, "No approved_with_comments → enrich_diff_context route found"
    enrich_step = recipe.steps["enrich_diff_context"]
    assert enrich_step.on_success == "resolve_review"


# T_IP_LOOP12
def test_review_pr_captures_review_verdict(recipe) -> None:
    """review_pr must capture verdict as review_verdict (not verdict) to avoid clobber."""
    step = recipe.steps["review_pr"]
    capture = step.capture or {}
    assert "review_verdict" in capture
    assert "result.verdict" in capture["review_verdict"].from_


# T_IP_LOOP13
def test_check_review_loop_with_args_has_previous_verdict(recipe) -> None:
    """check_review_loop with: must pass previous_verdict from context.review_verdict."""
    step = recipe.steps["check_review_loop"]
    assert "previous_verdict" in step.with_args
    assert "review_verdict" in step.with_args["previous_verdict"]


def test_capture_base_sha_captures_both_base_sha_and_merge_target(recipe) -> None:
    """bootstrap_clone must capture both base_sha and merge_target."""
    step = recipe.steps["clone"]
    capture = step.capture or {}
    assert "base_sha" in capture, (
        "clone (bootstrap_clone) must capture base_sha — the SHA of base_branch before any merge"
    )
    assert "merge_target" in capture, (
        "clone (bootstrap_clone) must capture merge_target — the fallback target branch name"
    )


def test_implementation_has_no_sprint_mode_ingredient() -> None:
    """implementation.yaml must not declare sprint_mode after sprint-prefix removal."""
    recipe = load_recipe(RECIPE_PATH)
    assert "sprint_mode" not in recipe.ingredients


def test_implementation_has_no_sprint_entry_step() -> None:
    """implementation.yaml must not have a sprint_entry step after sprint-prefix removal."""
    recipe = load_recipe(RECIPE_PATH)
    assert "sprint_entry" not in recipe.steps


def test_implementation_validates_clean_after_sprint_removal() -> None:
    """implementation.yaml must pass schema validation after sprint references removed."""
    recipe = load_recipe(RECIPE_PATH)
    errors = validate_recipe_structure(recipe)
    assert not errors, f"implementation.yaml failed validation after sprint removal: {errors}"


def test_done_unconfirmed_stop_exists(recipe) -> None:
    """implementation.yaml must have a done_unconfirmed stop step for merge-timeout paths."""
    assert "done_unconfirmed" in recipe.steps
    step = recipe.steps["done_unconfirmed"]
    assert step.action == "stop"
    assert len(step.message) >= 10
    assert "unconfirmed" in step.message.lower() or "timeout" in step.message.lower()


def test_done_step_uses_underscore_reason(recipe) -> None:
    """done step must use implementation_complete (underscore convention)."""
    step = recipe.steps["done"]
    assert step.action == "stop"
    assert '"implementation_complete"' in step.message


def test_done_no_changes_stop_exists(recipe) -> None:
    """implementation.yaml must have a done_no_changes stop step."""
    assert "done_no_changes" in recipe.steps
    step = recipe.steps["done_no_changes"]
    assert step.action == "stop"
    assert '"no_changes"' in step.message


def test_done_already_done_stop_exists(recipe) -> None:
    """implementation.yaml must have a done_already_done stop step."""
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


# ---------------------------------------------------------------------------
# T-ZW-7: retry_worktree on_context_limit
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("recipe_name", ["implementation", "remediation", "implementation-groups"])
def test_retry_worktree_has_on_context_limit(recipe_name: str) -> None:
    """retry_worktree step must have on_context_limit set for EARLY_STOP retry routing."""
    recipe = load_recipe(builtin_recipes_dir() / f"{recipe_name}.yaml")
    assert "retry_worktree" in recipe.steps, f"{recipe_name}: retry_worktree step not found"
    step = recipe.steps["retry_worktree"]
    assert step.on_context_limit is not None, (
        f"{recipe_name}: retry_worktree.on_context_limit must not be None "
        f"to support EARLY_STOP retry routing"
    )


# T-DM-5
@pytest.mark.parametrize("recipe_name", ["implementation", "remediation", "implementation-groups"])
def test_main_repo_guard_step_exists(recipe_name: str) -> None:
    """main_repo_guard step must exist with tool=run_python for dirty main repo recovery."""
    recipe = load_recipe(builtin_recipes_dir() / f"{recipe_name}.yaml")
    assert "main_repo_guard" in recipe.steps, f"{recipe_name}: main_repo_guard step not found"
    step = recipe.steps["main_repo_guard"]
    assert step.tool == "run_python", f"{recipe_name}: main_repo_guard must use run_python tool"
    assert "main_repo_guard" in step.with_args.get("callable", ""), (
        f"{recipe_name}: main_repo_guard callable must reference main_repo_guard"
    )


# T_DEV_IMP1
def test_fix_captures_deviation_manifest_path(recipe) -> None:
    """fix must capture deviation_manifest_path as optional_string."""
    step = recipe.steps["fix"]
    capture = step.capture or {}
    assert "deviation_manifest_path" in capture
    assert capture["deviation_manifest_path"].from_ == "${{ result.deviation_manifest_path }}"
    assert capture["deviation_manifest_path"].value_type == "optional_string"


# T_DEV_IMP2
def test_retry_worktree_captures_deviation_manifest_path(recipe) -> None:
    """retry_worktree must capture deviation_manifest_path as optional_string."""
    step = recipe.steps["retry_worktree"]
    capture = step.capture or {}
    assert "deviation_manifest_path" in capture
    assert capture["deviation_manifest_path"].from_ == "${{ result.deviation_manifest_path }}"
    assert capture["deviation_manifest_path"].value_type == "optional_string"


# T_DEV_IMP3
def test_audit_impl_forwards_deviation_manifest_path(recipe) -> None:
    """audit_impl must forward deviation_manifest_path kwarg and declare it optional."""
    step = recipe.steps["audit_impl"]
    assert step.with_args["skill_inputs"]["deviation_manifest_path"] == (
        "${{ context.deviation_manifest_path }}"
    )
    assert "deviation_manifest_path" in (step.optional_context_refs or [])


# T_DEV_IMP4
def test_merge_gate_fix_captures_deviation_manifest_path(recipe) -> None:
    """merge_gate_fix must capture deviation_manifest_path (flows to audit_impl)."""
    step = recipe.steps["merge_gate_fix"]
    capture = step.capture or {}
    assert "deviation_manifest_path" in capture
    assert capture["deviation_manifest_path"].value_type == "optional_string"


# T_DEV_CROSS1
@pytest.mark.parametrize("recipe_name", ["implementation", "remediation"])
def test_audit_impl_deviation_manifest_path_kwarg_consistency(recipe_name: str) -> None:
    """Both recipes must forward deviation_manifest_path identically to audit_impl."""
    recipe = load_recipe(builtin_recipes_dir() / f"{recipe_name}.yaml")
    step = recipe.steps["audit_impl"]
    assert step.with_args["skill_inputs"]["deviation_manifest_path"] == (
        "${{ context.deviation_manifest_path }}"
    )
    assert "deviation_manifest_path" in (step.optional_context_refs or [])


# ---------------------------------------------------------------------------
# #4476 Part C: scope-discipline gate (diff-size budget + scope-split loop)
# ---------------------------------------------------------------------------


def test_check_diff_size_step_exists(recipe) -> None:
    """check_diff_size must use run_python and gate on the current part's own diff via
    context.merge_target (not inputs.base_branch), so multi-part runs isolate per-part size."""
    assert "check_diff_size" in recipe.steps
    step = recipe.steps["check_diff_size"]
    assert step.tool == "run_python"
    assert "check_diff_size" in step.with_args.get("callable", "")
    assert step.with_args.get("base_branch") == "${{ context.merge_target }}"


def test_check_diff_size_routing(recipe) -> None:
    """check_diff_size routes over_budget to check_scope_split_loop, within_budget to test,
    and fails open to test so a broken advisory gate never halts otherwise-normal work."""
    step = recipe.steps["check_diff_size"]
    assert step.on_result is not None
    routes = {c.when: c.route for c in step.on_result.conditions if c.when}
    over_budget_routes = [route for when, route in routes.items() if "over_budget" in when]
    within_budget_routes = [route for when, route in routes.items() if "within_budget" in when]
    assert over_budget_routes == ["check_scope_split_loop"]
    assert within_budget_routes == ["test"]
    assert step.on_failure == "test"


def test_check_changes_routes_to_check_diff_size(recipe) -> None:
    """check_changes default arm must route to check_diff_size, not directly to test,
    so the diff-size gate always runs before tests when changes are present."""
    step = recipe.steps["check_changes"]
    assert step.on_result is not None
    default_conditions = [c for c in step.on_result.conditions if c.when is None]
    assert default_conditions, "No default (when=None) condition found"
    assert default_conditions[0].route == "check_diff_size"


def test_check_scope_split_loop_exists(recipe) -> None:
    """check_scope_split_loop uses check_loop_iteration; max_exceeded routes to
    release_issue_failure, default (not exceeded) routes back to plan for a re-plan cycle."""
    assert "check_scope_split_loop" in recipe.steps
    step = recipe.steps["check_scope_split_loop"]
    assert step.tool == "run_python"
    assert "check_loop_iteration" in step.with_args.get("callable", "")
    assert step.on_result is not None
    max_exceeded_conds = [
        c for c in step.on_result.conditions if c.when and "max_exceeded" in c.when
    ]
    assert max_exceeded_conds, "check_scope_split_loop must route max_exceeded"
    assert max_exceeded_conds[0].route == "release_issue_failure"
    default_conditions = [c for c in step.on_result.conditions if c.when is None]
    assert default_conditions, "No default (when=None) condition found"
    assert default_conditions[0].route == "pre_scope_replan_cleanup"


def test_implement_step_captures_scope_verdict(recipe) -> None:
    """implement must capture scope_verdict and route both split and proceed values
    explicitly via on_result."""
    step = recipe.steps["implement"]
    capture = step.capture or {}
    assert "scope_verdict" in capture
    assert step.on_result is not None
    when_texts = [c.when for c in step.on_result.conditions if c.when]
    assert any("split" in w for w in when_texts), "implement must route scope_verdict == split"
    assert any("proceed" in w for w in when_texts), "implement must route scope_verdict == proceed"


def test_retry_worktree_scope_verdict_routing(recipe) -> None:
    """retry_worktree must route both split and proceed scope_verdict values explicitly,
    and must no longer branch on phases_implemented > 0 (superseded by scope-verdict routing)."""
    step = recipe.steps["retry_worktree"]
    assert step.on_result is not None
    when_texts = [c.when for c in step.on_result.conditions if c.when]
    assert any("split" in w for w in when_texts), (
        "retry_worktree must route scope_verdict == split"
    )
    assert any("proceed" in w for w in when_texts), (
        "retry_worktree must route scope_verdict == proceed"
    )
    assert not any("phases_implemented" in w for w in when_texts), (
        "retry_worktree must not route on phases_implemented after scope-verdict routing"
    )


def test_plan_step_accepts_split_proposal(recipe) -> None:
    """plan must forward split_proposal_path to make-plan and declare it optional
    so a scope-split re-plan can feed the prior split proposal back in."""
    step = recipe.steps["plan"]
    assert step.with_args["skill_inputs"]["split_proposal_path"] == (
        "${{ context.split_proposal_path }}"
    )
    assert "split_proposal_path" in (step.optional_context_refs or [])


def test_scope_ingredients_exist(recipe) -> None:
    """diff_size_budget and scope_split_max_retries ingredients must exist with the
    documented defaults."""
    assert "diff_size_budget" in recipe.ingredients
    assert recipe.ingredients["diff_size_budget"].default == "6000"
    assert "scope_split_max_retries" in recipe.ingredients
    assert recipe.ingredients["scope_split_max_retries"].default == "2"


@pytest.mark.parametrize(
    "recipe_name", ["implementation", "remediation", "implementation-groups", "merge-prs"]
)
def test_scope_verdict_routed_explicitly_in_implement_and_retry(recipe_name: str) -> None:
    """Every bundled recipe's implement and retry_worktree steps must route both
    scope_verdict values (split, proceed) explicitly rather than relying on the
    catch-all arm, regardless of whether that recipe has a scope-split re-plan loop."""
    recipe = load_recipe(builtin_recipes_dir() / f"{recipe_name}.yaml")
    for step_name in ("implement", "retry_worktree"):
        assert step_name in recipe.steps, f"{recipe_name}: {step_name} step not found"
        step = recipe.steps[step_name]
        assert step.on_result is not None, f"{recipe_name}.{step_name} must declare on_result"
        when_texts = [c.when for c in step.on_result.conditions if c.when]
        assert any("split" in w for w in when_texts), (
            f"{recipe_name}.{step_name} must route scope_verdict == split explicitly"
        )
        assert any("proceed" in w for w in when_texts), (
            f"{recipe_name}.{step_name} must route scope_verdict == proceed explicitly"
        )
