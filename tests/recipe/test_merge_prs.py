"""Structural assertions for the bundled merge-prs recipe."""

from __future__ import annotations

import pytest

from autoskillit.recipe.io import builtin_recipes_dir, iter_steps_with_context, load_recipe

pytestmark = [pytest.mark.layer("recipe"), pytest.mark.small]


@pytest.fixture(scope="module")
def recipe():
    return load_recipe(builtin_recipes_dir() / "merge-prs.yaml")


def test_pmp_collect_and_check_impl_plans_step_exists(recipe) -> None:
    """collect_and_check_impl_plans step must exist in the recipe."""
    assert "collect_and_check_impl_plans" in recipe.steps, (
        "collect_and_check_impl_plans step is missing — it copies plan artifacts and gates "
        "audit_impl when no implementation plans were generated"
    )


def test_pmp_collect_and_check_impl_plans_is_run_cmd(recipe) -> None:
    """collect_and_check_impl_plans step must use the run_cmd tool."""
    step = recipe.steps["collect_and_check_impl_plans"]
    assert step.tool == "run_cmd"


def test_pmp_collect_and_check_impl_plans_cmd_contains_copy_and_count(recipe) -> None:
    """collect_and_check_impl_plans cmd must contain both cp and wc -l operations."""
    step = recipe.steps["collect_and_check_impl_plans"]
    cmd = step.with_args.get("cmd", "")
    assert "cp" in cmd, "cmd must contain a cp operation to copy plan files"
    assert "wc -l" in cmd, "cmd must contain wc -l to count implementation plans"


def test_pmp_check_impl_plans_excludes_pr_analysis_plan(recipe) -> None:
    """collect_and_check_impl_plans cmd must exclude pr_analysis_plan_*.md from its count.

    pr_analysis_plan_*.md is always written by analyze-prs and is not an
    implementation plan — including it would cause audit_impl to always run.
    """
    step = recipe.steps["collect_and_check_impl_plans"]
    cmd = step.with_args.get("cmd", "")
    assert "pr_analysis_plan" in cmd, (
        "collect_and_check_impl_plans must exclude pr_analysis_plan_*.md from its count — "
        "that file is always present and is not an implementation plan"
    )


def test_pmp_check_impl_plans_routes_to_open_integration_pr_on_empty(recipe) -> None:
    """collect_and_check_impl_plans routes to compute_domain_partitions when no impl plans exist.

    compute_domain_partitions is the pre-staging step before open_integration_pr.
    """
    step = recipe.steps["collect_and_check_impl_plans"]
    assert step.on_result is not None, "collect_and_check_impl_plans must use on_result routing"
    conds = step.on_result.conditions
    routes = {c.route for c in conds}
    assert "compute_domain_partitions" in routes, (
        "collect_and_check_impl_plans must route to compute_domain_partitions when count is 0"
    )
    zero_conds = [c for c in conds if c.when is not None and "0" in (c.when or "")]
    assert any(c.route == "compute_domain_partitions" for c in zero_conds), (
        "the compute_domain_partitions route must be guarded by a zero-count condition"
    )


def test_pmp_check_impl_plans_has_fallthrough_to_audit_impl(recipe) -> None:
    """collect_and_check_impl_plans fallthrough (when=None) must go to audit_impl."""
    step = recipe.steps["collect_and_check_impl_plans"]
    assert step.on_result is not None
    conds = step.on_result.conditions
    fallthrough = [c for c in conds if c.when is None]
    assert len(fallthrough) == 1, (
        "collect_and_check_impl_plans must have exactly one fallthrough condition"
    )
    assert fallthrough[0].route == "audit_impl", (
        "collect_and_check_impl_plans fallthrough must route to audit_impl when "
        "implementation plans exist"
    )


def test_pmp_audit_impl_has_skip_when_false(recipe) -> None:
    """audit_impl must still declare skip_when_false: inputs.audit (user-level toggle)."""
    step = recipe.steps["audit_impl"]
    assert step.skip_when_false == "inputs.audit"


def test_pmp_audit_impl_is_optional(recipe) -> None:
    """audit_impl must be marked optional (required by skip_when_false rule)."""
    step = recipe.steps["audit_impl"]
    assert step.optional is True


def test_pmp_plan_step_captures_all_plan_paths(recipe) -> None:
    """plan step must declare all_plan_paths in capture_list (accumulates per iteration)."""
    step = recipe.steps["plan"]
    assert "all_plan_paths" in step.capture_list, (
        "plan step must capture all_plan_paths via capture_list — needed so audit_impl receives "
        "explicit plan file paths instead of a directory"
    )
    assert "${{ result.plan_path }}" in step.capture_list["all_plan_paths"], (
        "all_plan_paths must accumulate result.plan_path on each loop iteration"
    )


def test_pmp_audit_impl_uses_all_plan_paths(recipe) -> None:
    """audit_impl skill_command must reference context.all_plan_paths, not inputs.plans_dir."""
    step = recipe.steps["audit_impl"]
    cmd = step.with_args["skill_command"]
    assert "${{ context.all_plan_paths }}" in cmd, (
        "audit_impl skill_command must reference context.all_plan_paths"
    )
    assert "inputs.plans_dir" not in cmd, (
        "audit_impl must not pass inputs.plans_dir — directory discovery is fragile "
        "and inconsistent with how every other recipe invokes audit-impl"
    )


def test_pmp_all_plan_paths_available_at_audit_impl(recipe) -> None:
    """all_plan_paths must be accumulated before audit_impl in declaration order.

    iter_steps_with_context gives the validator-view of what context keys are
    available at each step. all_plan_paths must appear before audit_impl.
    """
    assert recipe.steps
    for name, _step, available in iter_steps_with_context(recipe):
        if name == "audit_impl":
            assert "all_plan_paths" in available, (
                "all_plan_paths must be in available context before audit_impl — "
                "plan step must precede audit_impl in recipe declaration order"
            )
            break
    else:
        pytest.fail("audit_impl step not found in recipe")


def test_pmp_open_integration_pr_uses_run_skill(recipe) -> None:
    """open_integration_pr must use run_skill (not run_cmd)."""
    step = recipe.steps["open_integration_pr"]
    assert step.tool == "run_skill", (
        "open_integration_pr must use run_skill to invoke /autoskillit:open-integration-pr — "
        "the skill produces rich PR bodies with tables and arch-lens diagrams; "
        "run_cmd produces a minimal plain text PR"
    )


def test_pmp_open_integration_pr_calls_open_integration_pr_skill(recipe) -> None:
    """open_integration_pr skill_command must invoke /autoskillit:open-integration-pr."""
    step = recipe.steps["open_integration_pr"]
    cmd = step.with_args.get("skill_command", "")
    assert "/autoskillit:open-integration-pr" in cmd, (
        "open_integration_pr step must call /autoskillit:open-integration-pr skill"
    )


def test_pmp_open_integration_pr_captures_pr_url(recipe) -> None:
    """open_integration_pr must capture pr_url from the skill result."""
    step = recipe.steps["open_integration_pr"]
    assert "pr_url" in (step.capture or {}), (
        "open_integration_pr must capture pr_url from result — "
        "the open-integration-pr skill emits pr_url in its output"
    )


def test_pmp_open_integration_pr_passes_four_args(recipe) -> None:
    """skill_command must supply batch_branch, base_branch, pr_order_file, verdict."""
    step = recipe.steps["open_integration_pr"]
    cmd = step.with_args.get("skill_command", "")
    for arg in [
        "context.batch_branch",
        "inputs.base_branch",
        "context.pr_order_file",
        "context.verdict",
    ]:
        assert arg in cmd, f"open_integration_pr skill_command must include {arg}"


def test_pmp_base_branch_auto_detects(recipe) -> None:
    """base_branch must use auto-detect (default: empty string) from config."""
    ingredient = recipe.ingredients["base_branch"]
    assert ingredient.default == "", (
        "base_branch must use auto-detect (default: '') to resolve from config"
    )
    assert ingredient.required is False


def test_pmp_no_upstream_branch_ingredient(recipe) -> None:
    """upstream_branch ingredient must not exist — replaced by auto-detection."""
    assert "upstream_branch" not in recipe.ingredients


def test_pmp_setup_remote_routes_to_check_integration_exists(recipe) -> None:
    """setup_remote.on_success must route toward check_integration_exists.

    With check_repo_ci_event inserted as a non-blocking pre-step that captures
    ci_event, the chain is: setup_remote → check_repo_ci_event →
    check_integration_exists.  Both hops are required.
    """
    setup_successor = recipe.steps["setup_remote"].on_success
    assert setup_successor == "check_repo_ci_event", (
        f"setup_remote.on_success must be 'check_repo_ci_event', got {setup_successor!r}"
    )
    ci_event_successor = recipe.steps["check_repo_ci_event"].on_success
    assert ci_event_successor == "check_integration_exists", (
        f"check_repo_ci_event.on_success must be 'check_integration_exists',"
        f" got {ci_event_successor!r}"
    )


def test_pmp_has_check_integration_exists_step(recipe) -> None:
    """check_integration_exists step must exist and use run_cmd."""
    assert "check_integration_exists" in recipe.steps
    assert recipe.steps["check_integration_exists"].tool == "run_cmd"


def test_pmp_check_integration_exists_cmd_uses_base_branch(recipe) -> None:
    """check_integration_exists cmd must reference inputs.base_branch."""
    cmd = recipe.steps["check_integration_exists"].with_args["cmd"]
    assert "inputs.base_branch" in cmd


def test_pmp_check_integration_exists_routes_to_analyze_prs_on_success(recipe) -> None:
    """check_integration_exists routes to fetch_merge_queue_data when branch exists."""
    assert recipe.steps["check_integration_exists"].on_success == "fetch_merge_queue_data"


def test_pmp_check_integration_exists_routes_to_confirm_on_failure(recipe) -> None:
    """check_integration_exists must route to confirm step when branch is absent."""
    assert recipe.steps["check_integration_exists"].on_failure == "confirm_create_integration"


def test_pmp_has_confirm_create_integration_step(recipe) -> None:
    """confirm_create_integration must be a confirm action."""
    step = recipe.steps["confirm_create_integration"]
    assert step.action == "confirm"


def test_pmp_confirm_create_integration_routes_to_create_on_success(recipe) -> None:
    """User confirming must proceed to create_persistent_integration."""
    assert recipe.steps["confirm_create_integration"].on_success == "create_persistent_integration"


def test_pmp_confirm_create_integration_routes_to_escalate_on_failure(recipe) -> None:
    """User declining must route to escalate_stop."""
    assert recipe.steps["confirm_create_integration"].on_failure == "escalate_stop"


def test_pmp_has_create_persistent_integration_step(recipe) -> None:
    """create_persistent_integration must exist and use run_python callable."""
    assert "create_persistent_integration" in recipe.steps
    step = recipe.steps["create_persistent_integration"]
    assert step.tool == "run_python"
    assert (
        step.with_args["callable"] == "autoskillit.recipe._cmd_rpc.create_persistent_integration"
    )


def test_pmp_create_persistent_integration_passes_required_args(recipe) -> None:
    """create_persistent_integration must pass work_dir and base_branch."""
    args = recipe.steps["create_persistent_integration"].with_args
    assert "work_dir" in args
    assert "base_branch" in args


def test_pmp_create_persistent_integration_routes_to_analyze_prs(recipe) -> None:
    """After creating integration branch, routes to fetch_merge_queue_data."""
    assert recipe.steps["create_persistent_integration"].on_success == "fetch_merge_queue_data"


def test_pmp_merge_to_integration_removed(recipe) -> None:
    """merge_to_integration step must be removed — replaced by GitHub-API merge sequence."""
    assert "merge_to_integration" not in recipe.steps, (
        "merge_to_integration step still exists but must be replaced by the "
        "push_worktree_branch → create_conflict_pr → wait_for_conflict_ci"
        " → merge_conflict_pr sequence"
    )


def test_pmp_resolve_merge_conflicts_removed(recipe) -> None:
    """resolve_merge_conflicts step must be removed — it was only reachable from
    merge_to_integration (worktree_intact_rebase_aborted), which is also removed."""
    assert "resolve_merge_conflicts" not in recipe.steps, (
        "resolve_merge_conflicts still exists but must be removed — "
        "merge_to_integration (its only trigger) is gone in the GitHub-API merge flow"
    )


def test_pmp_commit_dirty_removed(recipe) -> None:
    """commit_dirty step must be removed — only reachable from resolve_merge_conflicts
    and retry_merge_after_resolution, both of which are removed."""
    assert "commit_dirty" not in recipe.steps, (
        "commit_dirty still exists but must be removed — "
        "all steps that route to it are removed in the GitHub-API merge flow"
    )


def test_pmp_has_push_worktree_branch_step(recipe) -> None:
    """push_worktree_branch step must exist to push the resolved worktree branch."""
    assert "push_worktree_branch" in recipe.steps, (
        "push_worktree_branch step is missing — required to push the conflict-resolution "
        "worktree branch to origin before creating a PR for GitHub-API merge"
    )


def test_pmp_has_create_conflict_pr_step(recipe) -> None:
    """create_conflict_pr step must exist to open a GitHub PR for the worktree branch."""
    assert "create_conflict_pr" in recipe.steps, (
        "create_conflict_pr step is missing — conflict resolution worktrees must be merged "
        "via GitHub PR (not local git) to enforce CI status checks"
    )
    step = recipe.steps["create_conflict_pr"]
    assert step.tool == "run_cmd"
    cmd = step.with_args.get("cmd", "")
    assert "gh pr create" in cmd


def test_pmp_has_wait_for_conflict_ci_step(recipe) -> None:
    """wait_for_conflict_ci step must exist and use the wait_for_ci MCP tool."""
    assert "wait_for_conflict_ci" in recipe.steps, (
        "wait_for_conflict_ci step is missing — CI must pass on the worktree branch "
        "before the conflict PR can be merged"
    )
    assert recipe.steps["wait_for_conflict_ci"].tool == "wait_for_ci"


def test_pmp_has_merge_conflict_pr_step(recipe) -> None:
    """merge_conflict_pr step must exist and use gh pr merge --squash."""
    assert "merge_conflict_pr" in recipe.steps, (
        "merge_conflict_pr step is missing — final merge of conflict-resolution PR"
    )
    step = recipe.steps["merge_conflict_pr"]
    assert step.tool == "run_cmd"
    cmd = step.with_args.get("cmd", "")
    assert "gh pr merge" in cmd
    assert "--squash" in cmd


# ---------------------------------------------------------------------------
# CI watch PR tests
# ---------------------------------------------------------------------------


def test_ci_watch_pr_exists_with_correct_tool(recipe) -> None:
    """ci_watch_pr step must use wait_for_ci tool."""
    assert "ci_watch_pr" in recipe.steps
    step = recipe.steps["ci_watch_pr"]
    assert step.tool == "wait_for_ci"


def test_ci_watch_pr_uses_batch_branch(recipe) -> None:
    """ci_watch_pr must use context.batch_branch as the branch parameter."""
    step = recipe.steps["ci_watch_pr"]
    assert "context.batch_branch" in step.with_args["branch"]


def test_ci_watch_pr_routing(recipe) -> None:
    """ci_watch_pr on_result success -> register_clone_success; on_failure -> diagnose_ci."""
    step = recipe.steps["ci_watch_pr"]
    assert step.on_result is not None, "ci_watch_pr must use on_result predicate routing"
    result_routes = {c.route for c in step.on_result.conditions}
    assert "patch_token_summary" in result_routes
    assert step.on_failure == "diagnose_ci"


def test_ci_watch_pr_no_inline_shell(recipe) -> None:
    """ci_watch_pr must not contain inline shell commands."""
    step = recipe.steps["ci_watch_pr"]
    assert "cmd" not in step.with_args


def test_ci_watch_pr_has_no_capture(recipe) -> None:
    """ci_watch_pr must not capture — no downstream consumer in merge-prs."""
    step = recipe.steps["ci_watch_pr"]
    assert not step.capture


# ── B-series: Mergeability Gate + Review Cycle ──────────────────────────────


def test_pmp_open_integration_pr_routes_to_wait_for_mergeability(recipe) -> None:
    """B1: open_integration_pr.on_success must route to wait_for_review_pr_mergeability."""
    step = recipe.steps["open_integration_pr"]
    assert step.on_success == "wait_for_review_pr_mergeability"


def test_pmp_has_wait_for_review_pr_mergeability_step(recipe) -> None:
    """B2: wait_for_review_pr_mergeability step must exist and use run_python callable."""
    assert "wait_for_review_pr_mergeability" in recipe.steps
    step = recipe.steps["wait_for_review_pr_mergeability"]
    assert step.tool == "run_python"
    assert (
        step.with_args["callable"] == "autoskillit.recipe._cmd_rpc.wait_for_review_pr_mergeability"
    )


def test_pmp_wait_for_mergeability_captures_review_pr_number(recipe) -> None:
    """B3: wait_for_review_pr_mergeability must capture review_pr_number."""
    step = recipe.steps["wait_for_review_pr_mergeability"]
    assert "review_pr_number" in step.capture


def test_pmp_wait_for_mergeability_routes_to_check_mergeability(recipe) -> None:
    """B4: wait_for_review_pr_mergeability.on_success must route to check_mergeability."""
    step = recipe.steps["wait_for_review_pr_mergeability"]
    assert step.on_success == "check_mergeability"


def test_pmp_has_check_mergeability_step(recipe) -> None:
    """B5: check_mergeability step must exist and use check_pr_mergeable tool."""
    assert "check_mergeability" in recipe.steps
    step = recipe.steps["check_mergeability"]
    assert step.tool == "check_pr_mergeable"


def test_pmp_check_mergeability_routes_mergeable_to_review_pr_integration(recipe) -> None:
    """B6: check_mergeability routes MERGEABLE to annotate_pr_diff."""
    step = recipe.steps["check_mergeability"]
    assert step.on_result is not None
    conditions = step.on_result.conditions
    mergeable_routes = [c for c in conditions if c.when and "MERGEABLE" in c.when]
    assert any(c.route == "annotate_pr_diff" for c in mergeable_routes)


def test_pmp_check_mergeability_routes_conflicting_to_resolve_integration_conflicts(
    recipe,
) -> None:
    """B7: check_mergeability on_result must route CONFLICTING to resolve_integration_conflicts."""
    step = recipe.steps["check_mergeability"]
    assert step.on_result is not None
    conditions = step.on_result.conditions
    conflicting_routes = [c for c in conditions if c.when and "CONFLICTING" in c.when]
    assert any(c.route == "resolve_integration_conflicts" for c in conflicting_routes)


def test_pmp_has_resolve_integration_conflicts_step(recipe) -> None:
    """B8: resolve_integration_conflicts must exist with run_skill and resolve-merge-conflicts."""
    assert "resolve_integration_conflicts" in recipe.steps
    step = recipe.steps["resolve_integration_conflicts"]
    assert step.tool == "run_skill"
    assert "resolve-merge-conflicts" in step.with_args.get("skill_command", "")


def test_pmp_resolve_integration_conflicts_routes_to_force_push(recipe) -> None:
    """B9: resolve_integration_conflicts must route to force_push_and_wait_mergeability."""
    step = recipe.steps["resolve_integration_conflicts"]
    # Step uses on_result conditions; the default (no-when) bare route must route to force_push
    assert step.on_result is not None
    conditions = step.on_result.conditions
    default_routes = [c for c in conditions if c.when is None]
    assert any(c.route == "force_push_and_wait_mergeability" for c in default_routes)


def test_pmp_has_force_push_and_wait_mergeability_step(recipe) -> None:
    """B10: force_push_and_wait_mergeability must exist with run_python callable."""
    assert "force_push_and_wait_mergeability" in recipe.steps
    step = recipe.steps["force_push_and_wait_mergeability"]
    assert step.tool == "run_python"
    assert (
        step.with_args["callable"]
        == "autoskillit.recipe._cmd_rpc.force_push_and_wait_mergeability"
    )


def test_pmp_force_push_and_wait_mergeability_routes_to_check_post_rebase(
    recipe,
) -> None:
    """B25: force_push_and_wait_mergeability.on_success must route to check_mergeability_post_rebase."""  # noqa: E501
    step = recipe.steps["force_push_and_wait_mergeability"]
    assert step.on_success == "check_mergeability_post_rebase"


def test_pmp_force_push_and_wait_mergeability_on_failure(recipe) -> None:
    """force_push_and_wait_mergeability.on_failure must route to register_clone_failure."""
    step = recipe.steps["force_push_and_wait_mergeability"]
    assert step.on_failure == "register_clone_failure"


def test_pmp_has_check_mergeability_post_rebase_step(recipe) -> None:
    """B11: check_mergeability_post_rebase step must exist with check_pr_mergeable tool."""
    assert "check_mergeability_post_rebase" in recipe.steps
    step = recipe.steps["check_mergeability_post_rebase"]
    assert step.tool == "check_pr_mergeable"


def test_pmp_check_mergeability_post_rebase_routes_mergeable_to_review(recipe) -> None:
    """B12: post_rebase mergeability check routes MERGEABLE to annotate_pr_diff."""
    step = recipe.steps["check_mergeability_post_rebase"]
    assert step.on_result is not None
    conditions = step.on_result.conditions
    mergeable_routes = [c for c in conditions if c.when and "MERGEABLE" in c.when]
    assert any(c.route == "annotate_pr_diff" for c in mergeable_routes)


def test_pmp_has_review_pr_integration_step(recipe) -> None:
    """B13: review_pr_integration step must exist with run_skill tool and review-pr."""
    assert "review_pr_integration" in recipe.steps
    step = recipe.steps["review_pr_integration"]
    assert step.tool == "run_skill"
    assert "review-pr" in step.with_args.get("skill_command", "")


def test_pmp_review_pr_integration_uses_batch_branch(recipe) -> None:
    """B14: review_pr_integration skill_command must reference context.batch_branch."""
    step = recipe.steps["review_pr_integration"]
    assert "context.batch_branch" in step.with_args.get("skill_command", "")


def test_pmp_review_pr_integration_routes_changes_requested_to_resolve_review(recipe) -> None:
    """B15: on_result must route changes_requested to resolve_review_integration."""
    step = recipe.steps["review_pr_integration"]
    assert step.on_result is not None
    conditions = step.on_result.conditions
    cr_routes = [c for c in conditions if c.when and "changes_requested" in c.when]
    assert any(c.route == "resolve_review_integration" for c in cr_routes)


def test_pmp_review_pr_integration_routes_needs_human_explicitly(recipe) -> None:
    """B16: review_pr_integration must have an explicit needs_human condition (not fallthrough)."""
    step = recipe.steps["review_pr_integration"]
    assert step.on_result is not None
    conditions = step.on_result.conditions
    needs_human_routes = [c for c in conditions if c.when and "needs_human" in c.when]
    assert needs_human_routes, (
        "review_pr_integration must have an explicit needs_human route to satisfy "
        "the unrouted-verdict-value semantic rule"
    )


def test_pmp_has_resolve_review_integration_step(recipe) -> None:
    """B17: resolve_review_integration step must exist with run_skill tool and resolve-review."""
    assert "resolve_review_integration" in recipe.steps
    step = recipe.steps["resolve_review_integration"]
    assert step.tool == "run_skill"
    assert "resolve-review" in step.with_args.get("skill_command", "")


def test_pmp_resolve_review_integration_has_retries(recipe) -> None:
    """B18: resolve_review_integration must have retries == 2."""
    step = recipe.steps["resolve_review_integration"]
    assert step.retries == 2


def test_pmp_resolve_review_integration_routes_to_re_push(recipe) -> None:
    """B19: resolve_review_integration routes real_fix to re_push."""
    step = recipe.steps["resolve_review_integration"]
    assert step.on_success is None, (
        "resolve_review_integration must use on_result: verdict dispatch"
    )
    assert step.on_result is not None, "resolve_review_integration must have on_result: block"
    push_routes = [c.route for c in step.on_result.conditions if c.when and "real_fix" in c.when]
    assert any("re_push_review_integration" in r for r in push_routes), (
        "resolve_review_integration must route verdict=real_fix to re_push_review_integration"
    )


def test_pmp_has_re_push_review_integration_step(recipe) -> None:
    """B20: re_push_review_integration step must exist with push_to_remote tool."""
    assert "re_push_review_integration" in recipe.steps
    step = recipe.steps["re_push_review_integration"]
    assert step.tool == "push_to_remote"


def test_pmp_re_push_review_integration_uses_batch_branch(recipe) -> None:
    """B21: re_push_review_integration must pass context.batch_branch as branch arg."""
    step = recipe.steps["re_push_review_integration"]
    assert "context.batch_branch" in step.with_args.get("branch", "")


def test_pmp_re_push_review_integration_routes_to_derive_batch_ci_event(recipe) -> None:
    """B22: re_push_review_integration.on_success must route through derive_batch_ci_event."""
    step = recipe.steps["re_push_review_integration"]
    assert step.on_success == "derive_batch_ci_event"


def test_pmp_setup_remote_uses_context_remote_url(recipe) -> None:
    """REQ-C7-03: setup_remote must use context.remote_url, not inputs.source_dir.

    When source_dir defaults to "", git -C "" fails with 'fatal: cannot change to '''.
    context.remote_url is already captured from the clone step and holds the actual
    GitHub remote URL.
    """
    step = recipe.steps["setup_remote"]
    cmd = step.with_args.get("cmd", "")
    assert "context.remote_url" in cmd, (
        "setup_remote must use context.remote_url instead of "
        "git -C ${{ inputs.source_dir }} remote get-url origin — "
        "source_dir defaults to empty string causing git -C '' to fail"
    )


def test_pmp_setup_remote_not_using_inputs_source_dir(recipe) -> None:
    """REQ-C7-03: setup_remote must not reference inputs.source_dir in git -C."""
    step = recipe.steps["setup_remote"]
    cmd = step.with_args.get("cmd", "")
    # The old pattern that fails on empty source_dir
    assert "git -C" not in cmd or "inputs.source_dir" not in cmd, (
        "setup_remote must not use 'git -C ${{ inputs.source_dir }}' — "
        "fails when source_dir is empty (the default value)"
    )


def test_pmp_push_ejected_fix_has_force_true(recipe) -> None:
    """T13: push_ejected_fix must have force='true' (post-rebase push needs --force-with-lease)."""
    assert "push_ejected_fix" in recipe.steps
    step = recipe.steps["push_ejected_fix"]
    assert step.tool == "push_to_remote"
    assert step.with_args.get("force") == "true", (
        "push_ejected_fix must include force='true' — it follows a resolve-merge-conflicts "
        "step that rewrites commit SHAs, so a non-fast-forward force push is required"
    )


def test_annotate_pr_diff_passes_local_review_rounds_explicitly(recipe) -> None:
    """annotate_pr_diff step must include local_review_rounds in its with_args.

    When local_review_rounds is omitted, the Python default '' is passed to
    annotate_pr_diff, forcing review_mode='github' regardless of user config.
    Making it explicit documents the intent and respects the recipe's own default.
    """
    step = recipe.steps["annotate_pr_diff"]
    assert "local_review_rounds" in step.with_args, (
        "annotate_pr_diff step must explicitly pass local_review_rounds to remove "
        "the dependency on the Python default '' which forces github review mode"
    )


def test_merge_pr_captures_merged(recipe) -> None:
    """merge_pr step captures result.merged."""
    step = recipe.steps["merge_pr"]
    assert step.capture is not None
    assert "merged" in step.capture
    assert "${{ result.merged }}" in step.capture["merged"]


def test_merge_pr_routes_merged_false_to_failure(recipe) -> None:
    """merge_pr on_result routes merged=false to register_clone_failure."""
    step = recipe.steps["merge_pr"]
    assert step.on_result is not None
    conditions = step.on_result.conditions
    merged_false_conds = [
        c for c in conditions if c.when and "merged" in c.when and "false" in c.when
    ]
    assert merged_false_conds, "merge_pr must have a when clause for merged==false"
    assert merged_false_conds[0].route == "register_clone_failure", (
        "merged=false route must go to register_clone_failure, not "
        f"{merged_false_conds[0].route!r}"
    )


def test_wait_for_conflict_ci_uses_worktree_cwd(recipe) -> None:
    """wait_for_conflict_ci must use worktree_path, not work_dir, for correct SHA inference."""
    step = recipe.steps["wait_for_conflict_ci"]
    cwd = step.with_args.get("cwd", "")
    assert "worktree_path" in cwd, (
        f"wait_for_conflict_ci cwd must reference worktree_path for correct SHA inference, "
        f"got: {cwd}"
    )
    assert "work_dir" not in cwd, (
        f"wait_for_conflict_ci cwd must NOT use work_dir (clone root has wrong branch), got: {cwd}"
    )


def test_wait_ci_pre_enqueue_has_explicit_head_sha_or_matching_cwd(recipe) -> None:
    """wait_ci_pre_enqueue: cwd must match branch or head_sha must be explicit."""
    step = recipe.steps["wait_ci_pre_enqueue"]
    cwd = step.with_args.get("cwd", "")
    head_sha = step.with_args.get("head_sha")
    branch = step.with_args.get("branch", "")

    cwd_matches_branch = (
        branch and cwd and (branch.split(".")[-1] in cwd.split(".")[-1] or "worktree" in cwd)
    )
    assert cwd_matches_branch or head_sha is not None, (
        f"wait_ci_pre_enqueue watches branch={branch!r} but cwd={cwd!r} does not "
        f"reference the same context and head_sha is not explicit. "
        f"Either cwd must check out the watched branch or head_sha must be provided."
    )


def test_capture_pr_head_sha_step_exists(recipe) -> None:
    """capture_pr_head_sha step must exist and route to derive_pre_enqueue_ci_event."""
    assert "capture_pr_head_sha" in recipe.steps
    step = recipe.steps["capture_pr_head_sha"]
    assert step.tool == "run_cmd"
    assert step.on_success == "derive_pre_enqueue_ci_event"


def test_get_current_pr_branch_routes_to_capture(recipe) -> None:
    """get_current_pr_branch must route to capture_pr_head_sha."""
    step = recipe.steps["get_current_pr_branch"]
    assert step.on_success == "capture_pr_head_sha", (
        f"get_current_pr_branch.on_success must be 'capture_pr_head_sha', got {step.on_success!r}"
    )


def test_wait_for_conflict_ci_has_auto_trigger(recipe) -> None:
    """wait_for_conflict_ci has auto_trigger: true."""
    step = recipe.steps["wait_for_conflict_ci"]
    assert step.with_args.get("auto_trigger") is True


def test_wait_for_conflict_ci_timed_out_routes_to_guard(recipe) -> None:
    """wait_for_conflict_ci timed_out route goes to check_conflict_ci_loop, not self."""
    step = recipe.steps["wait_for_conflict_ci"]
    assert step.on_result is not None
    timed_out_conds = [c for c in step.on_result.conditions if c.when and "timed_out" in c.when]
    assert timed_out_conds, "wait_for_conflict_ci must have a timed_out condition"
    assert timed_out_conds[0].route == "check_conflict_ci_loop", (
        "wait_for_conflict_ci timed_out must route to check_conflict_ci_loop, not self"
    )


def test_check_conflict_ci_loop_exists_with_correct_pattern(recipe) -> None:
    """check_conflict_ci_loop step uses check_loop_iteration with max_iterations: 2."""
    assert "check_conflict_ci_loop" in recipe.steps
    step = recipe.steps["check_conflict_ci_loop"]
    assert step.tool == "run_python"
    assert "check_loop_iteration" in step.with_args.get("callable", "")
    assert step.with_args.get("max_iterations") == "2"
    assert step.on_result is not None
    max_exceeded_conds = [
        c
        for c in step.on_result.conditions
        if c.when and "max_exceeded" in c.when and "true" in c.when
    ]
    assert max_exceeded_conds, "check_conflict_ci_loop must route max_exceeded==true"
    assert max_exceeded_conds[0].route == "register_clone_failure"


def test_ci_watch_pr_has_auto_trigger(recipe) -> None:
    """ci_watch_pr has auto_trigger: true."""
    step = recipe.steps["ci_watch_pr"]
    assert step.with_args.get("auto_trigger") is True


def test_ci_watch_pr_timed_out_routes_to_guard(recipe) -> None:
    """ci_watch_pr timed_out route goes to check_ci_watch_pr_loop, not self."""
    step = recipe.steps["ci_watch_pr"]
    assert step.on_result is not None
    timed_out_conds = [c for c in step.on_result.conditions if c.when and "timed_out" in c.when]
    assert timed_out_conds, "ci_watch_pr must have a timed_out condition"
    assert timed_out_conds[0].route == "check_ci_watch_pr_loop", (
        "ci_watch_pr timed_out must route to check_ci_watch_pr_loop, not self"
    )


def test_check_ci_watch_pr_loop_exists_with_correct_pattern(recipe) -> None:
    """check_ci_watch_pr_loop step uses check_loop_iteration with max_iterations: 2."""
    assert "check_ci_watch_pr_loop" in recipe.steps
    step = recipe.steps["check_ci_watch_pr_loop"]
    assert step.tool == "run_python"
    assert "check_loop_iteration" in step.with_args.get("callable", "")
    assert step.with_args.get("max_iterations") == "2"
    assert step.on_result is not None
    max_exceeded_conds = [
        c
        for c in step.on_result.conditions
        if c.when and "max_exceeded" in c.when and "true" in c.when
    ]
    assert max_exceeded_conds, "check_ci_watch_pr_loop must route max_exceeded==true"
    assert max_exceeded_conds[0].route == "register_clone_failure"


def test_all_wait_for_ci_steps_have_remote_url(recipe) -> None:
    """Every wait_for_ci step must include remote_url in its with_args."""
    for step_name, step in recipe.steps.items():
        if step.tool != "wait_for_ci":
            continue
        assert "remote_url" in step.with_args, (
            f"{step_name}: wait_for_ci step must pass remote_url to prevent "
            f"file:// clone path fallback during repo resolution"
        )


def test_wait_for_conflict_ci_uses_dedicated_ci_event(recipe) -> None:
    """wait_for_conflict_ci must use a branch-specific ci_event, not the global one.

    The global context.ci_event is derived for inputs.base_branch (main), but
    wait_for_conflict_ci watches context.worktree_branch_name — a completely
    different branch. Using the global ci_event causes no_runs timeouts when
    CI only triggers on main pushes.
    """
    step = recipe.steps["wait_for_conflict_ci"]
    event = step.with_args.get("event", "")
    assert "context.ci_event" not in event, (
        "wait_for_conflict_ci must NOT use context.ci_event — it was derived for "
        "inputs.base_branch (main), not the worktree branch being watched. "
        "Use a dedicated context.conflict_ci_event instead."
    )
    assert "context.conflict_ci_event" in event, (
        "wait_for_conflict_ci must use context.conflict_ci_event — "
        "the ci_event derived for the actual worktree branch"
    )


def test_ci_watch_pr_uses_dedicated_ci_event(recipe) -> None:
    """ci_watch_pr must use a branch-specific ci_event, not the global one."""
    step = recipe.steps["ci_watch_pr"]
    event = step.with_args.get("event", "")
    assert "context.ci_event" not in event, (
        "ci_watch_pr must NOT use context.ci_event — it was derived for "
        "inputs.base_branch (main), not context.batch_branch being watched. "
        "Use a dedicated context.batch_ci_event instead."
    )
    assert "context.batch_ci_event" in event, (
        "ci_watch_pr must use context.batch_ci_event — "
        "the ci_event derived for the actual batch branch"
    )


def test_wait_ci_pre_enqueue_uses_dedicated_ci_event(recipe) -> None:
    """wait_ci_pre_enqueue must use a branch-specific ci_event."""
    step = recipe.steps["wait_ci_pre_enqueue"]
    event = step.with_args.get("event", "")
    assert "context.ci_event" not in event, (
        "wait_ci_pre_enqueue must NOT use context.ci_event — it was derived for "
        "inputs.base_branch (main), not context.current_pr_branch being watched. "
        "Use a dedicated context.pre_enqueue_ci_event instead."
    )
    assert "context.pre_enqueue_ci_event" in event, (
        "wait_ci_pre_enqueue must use context.pre_enqueue_ci_event — "
        "the ci_event derived for the actual PR branch"
    )


def test_ci_watch_post_queue_fix_uses_dedicated_ci_event(recipe) -> None:
    """ci_watch_post_queue_fix must use a branch-specific ci_event."""
    step = recipe.steps["ci_watch_post_queue_fix"]
    event = step.with_args.get("event", "")
    assert "context.ci_event" not in event, (
        "ci_watch_post_queue_fix must NOT use context.ci_event — it was derived for "
        "inputs.base_branch (main), not context.ejected_pr_branch being watched. "
        "Use a dedicated context.post_queue_ci_event instead."
    )
    assert "context.post_queue_ci_event" in event, (
        "ci_watch_post_queue_fix must use context.post_queue_ci_event — "
        "the ci_event derived for the actual ejected PR branch"
    )


def test_derive_conflict_ci_event_step_exists(recipe) -> None:
    """derive_conflict_ci_event must exist to re-derive ci_event for the worktree branch."""
    assert "derive_conflict_ci_event" in recipe.steps, (
        "derive_conflict_ci_event step is missing — needed to derive ci_event for the "
        "actual worktree branch before wait_for_conflict_ci runs"
    )
    step = recipe.steps["derive_conflict_ci_event"]
    assert step.tool == "check_repo_merge_state"
    assert "worktree_branch_name" in step.with_args.get("branch", "")


def test_derive_pre_enqueue_ci_event_step_exists(recipe) -> None:
    """derive_pre_enqueue_ci_event must exist to re-derive ci_event for the PR branch."""
    assert "derive_pre_enqueue_ci_event" in recipe.steps, (
        "derive_pre_enqueue_ci_event step is missing — needed to derive ci_event for the "
        "actual PR branch before wait_ci_pre_enqueue runs"
    )
    step = recipe.steps["derive_pre_enqueue_ci_event"]
    assert step.tool == "check_repo_merge_state"
    assert "current_pr_branch" in step.with_args.get("branch", "")


def test_derive_post_queue_ci_event_step_exists(recipe) -> None:
    """derive_post_queue_ci_event must exist to re-derive ci_event for the ejected PR branch."""
    assert "derive_post_queue_ci_event" in recipe.steps, (
        "derive_post_queue_ci_event step is missing — needed to derive ci_event for the "
        "actual ejected PR branch before ci_watch_post_queue_fix runs"
    )
    step = recipe.steps["derive_post_queue_ci_event"]
    assert step.tool == "check_repo_merge_state"
    assert "ejected_pr_branch" in step.with_args.get("branch", "")


def test_derive_batch_ci_event_step_exists(recipe) -> None:
    """derive_batch_ci_event must exist to re-derive ci_event for the batch branch."""
    assert "derive_batch_ci_event" in recipe.steps, (
        "derive_batch_ci_event step is missing — needed to derive ci_event for the "
        "actual batch branch before ci_watch_pr runs"
    )
    step = recipe.steps["derive_batch_ci_event"]
    assert step.tool == "check_repo_merge_state"
    assert "batch_branch" in step.with_args.get("branch", "")


def test_create_conflict_pr_routes_to_derive_conflict_ci_event(recipe) -> None:
    """create_conflict_pr.on_success must route to derive_conflict_ci_event."""
    step = recipe.steps["create_conflict_pr"]
    assert step.on_success == "derive_conflict_ci_event", (
        f"create_conflict_pr.on_success must be "
        f"'derive_conflict_ci_event', got {step.on_success!r}"
    )


def test_handle_conflict_no_runs_step_exists(recipe) -> None:
    """handle_conflict_no_runs must exist for no_runs recovery in conflict CI path."""
    assert "handle_conflict_no_runs" in recipe.steps, (
        "handle_conflict_no_runs step is missing — needed to handle no_runs conclusion "
        "from wait_for_conflict_ci (mirrors implementation.yaml's handle_no_ci_runs)"
    )
    step = recipe.steps["handle_conflict_no_runs"]
    assert step.tool == "check_repo_merge_state"


def test_wait_for_conflict_ci_routes_no_runs_to_handler(recipe) -> None:
    """wait_for_conflict_ci must route no_runs to handle_conflict_no_runs."""
    step = recipe.steps["wait_for_conflict_ci"]
    assert step.on_result is not None
    no_runs_conds = [c for c in step.on_result.conditions if c.when and "no_runs" in c.when]
    assert no_runs_conds, "wait_for_conflict_ci must have an explicit no_runs route"
    assert no_runs_conds[0].route == "handle_conflict_no_runs", (
        f"wait_for_conflict_ci no_runs must route to handle_conflict_no_runs, "
        f"got {no_runs_conds[0].route!r}"
    )


def test_wait_for_ci_steps_have_consistent_cwd(recipe) -> None:
    """Every wait_for_ci step's cwd must be consistent with its branch parameter."""
    for step_name, step in recipe.steps.items():
        if step.tool != "wait_for_ci":
            continue
        branch = step.with_args.get("branch", "")
        cwd = step.with_args.get("cwd", "")
        head_sha = step.with_args.get("head_sha")

        if "worktree_branch" in branch and not head_sha:
            assert "worktree" in cwd, (
                f"{step_name}: watches worktree branch ({branch}) "
                f"but cwd ({cwd}) is not a worktree path and no head_sha is explicit"
            )
