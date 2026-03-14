"""Structural assertions for the bundled merge-prs recipe."""

from __future__ import annotations

import pytest

from autoskillit.recipe.io import builtin_recipes_dir, iter_steps_with_context, load_recipe


@pytest.fixture(scope="module")
def recipe():
    return load_recipe(builtin_recipes_dir() / "merge-prs.yaml")


def test_pmp_check_impl_plans_step_exists(recipe) -> None:
    """check_impl_plans step must exist in the recipe."""
    assert "check_impl_plans" in recipe.steps, (
        "check_impl_plans step is missing — it gates audit_impl when no "
        "implementation plans were generated"
    )


def test_pmp_collect_artifacts_routes_to_check_impl_plans(recipe) -> None:
    """collect_artifacts.on_success must route to check_impl_plans, not audit_impl."""
    step = recipe.steps["collect_artifacts"]
    assert step.on_success == "check_impl_plans", (
        "collect_artifacts.on_success must route to check_impl_plans, not audit_impl — "
        "the check step decides whether audit is meaningful"
    )


def test_pmp_collect_artifacts_failure_routes_to_check_impl_plans(recipe) -> None:
    """collect_artifacts.on_failure must also route to check_impl_plans."""
    step = recipe.steps["collect_artifacts"]
    assert step.on_failure == "check_impl_plans", (
        "collect_artifacts.on_failure must route to check_impl_plans "
        "so the gate runs even when artifact copying fails"
    )


def test_pmp_check_impl_plans_is_run_cmd(recipe) -> None:
    """check_impl_plans step must use the run_cmd tool."""
    step = recipe.steps["check_impl_plans"]
    assert step.tool == "run_cmd"


def test_pmp_check_impl_plans_excludes_pr_analysis_plan(recipe) -> None:
    """check_impl_plans cmd must exclude pr_analysis_plan_*.md from its count.

    pr_analysis_plan_*.md is always written by analyze-prs and is not an
    implementation plan — including it would cause audit_impl to always run.
    """
    step = recipe.steps["check_impl_plans"]
    cmd = step.with_args.get("cmd", "")
    assert "pr_analysis_plan" in cmd, (
        "check_impl_plans must exclude pr_analysis_plan_*.md from its count — "
        "that file is always present and is not an implementation plan"
    )


def test_pmp_check_impl_plans_routes_to_create_review_pr_on_empty(recipe) -> None:
    """check_impl_plans must route to create_review_pr when no impl plans exist."""
    step = recipe.steps["check_impl_plans"]
    assert step.on_result is not None, "check_impl_plans must use on_result routing"
    conds = step.on_result.conditions
    routes = {c.route for c in conds}
    assert "create_review_pr" in routes, (
        "check_impl_plans must route to create_review_pr when count is 0 — "
        "skipping audit_impl when no implementation plans exist"
    )
    zero_conds = [c for c in conds if c.when is not None and "0" in (c.when or "")]
    assert any(c.route == "create_review_pr" for c in zero_conds), (
        "the create_review_pr route must be guarded by a zero-count condition"
    )


def test_pmp_check_impl_plans_has_fallthrough_to_audit_impl(recipe) -> None:
    """check_impl_plans fallthrough (when=None) must go to audit_impl."""
    step = recipe.steps["check_impl_plans"]
    assert step.on_result is not None
    conds = step.on_result.conditions
    fallthrough = [c for c in conds if c.when is None]
    assert len(fallthrough) == 1, "check_impl_plans must have exactly one fallthrough condition"
    assert fallthrough[0].route == "audit_impl", (
        "check_impl_plans fallthrough must route to audit_impl when implementation plans exist"
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


def test_pmp_create_review_pr_uses_run_skill(recipe) -> None:
    """create_review_pr must use run_skill (not run_cmd)."""
    step = recipe.steps["create_review_pr"]
    assert step.tool == "run_skill", (
        "create_review_pr must use run_skill to invoke /autoskillit:create-review-pr — "
        "the skill produces rich PR bodies with tables and arch-lens diagrams; "
        "run_cmd produces a minimal plain text PR"
    )


def test_pmp_create_review_pr_calls_create_review_pr_skill(recipe) -> None:
    """create_review_pr skill_command must invoke /autoskillit:create-review-pr."""
    step = recipe.steps["create_review_pr"]
    cmd = step.with_args.get("skill_command", "")
    assert "/autoskillit:create-review-pr" in cmd, (
        "create_review_pr step must call /autoskillit:create-review-pr skill"
    )


def test_pmp_create_review_pr_captures_pr_url(recipe) -> None:
    """create_review_pr must capture pr_url from the skill result."""
    step = recipe.steps["create_review_pr"]
    assert "pr_url" in (step.capture or {}), (
        "create_review_pr must capture pr_url from result — "
        "the create-review-pr skill emits pr_url in its output"
    )


def test_pmp_create_review_pr_passes_four_args(recipe) -> None:
    """skill_command must supply integration_branch, base_branch, pr_order_file, verdict."""
    step = recipe.steps["create_review_pr"]
    cmd = step.with_args.get("skill_command", "")
    for arg in [
        "context.integration_branch",
        "inputs.base_branch",
        "context.pr_order_file",
        "context.verdict",
    ]:
        assert arg in cmd, f"create_review_pr skill_command must include {arg}"


def test_pmp_base_branch_has_no_silent_default(recipe) -> None:
    """base_branch must have no default — callers must specify it explicitly.

    Silently defaulting base_branch to 'integration' would break existing callers
    that previously relied on 'main' as the target.  Requiring an explicit value
    forces callers to opt in to the integration-branch feature consciously.
    """
    ingredient = recipe.ingredients["base_branch"]
    assert ingredient.default is None, (
        "base_branch must not have a default — callers must pass base_branch "
        "explicitly (e.g. 'integration' or 'main') to avoid silent target changes"
    )


def test_pmp_has_upstream_branch_ingredient(recipe) -> None:
    """upstream_branch ingredient must exist with default 'main'."""
    assert "upstream_branch" in recipe.ingredients
    assert recipe.ingredients["upstream_branch"].default == "main"


def test_pmp_setup_remote_routes_to_check_integration_exists(recipe) -> None:
    """setup_remote.on_success must route to check_integration_exists, not analyze_prs."""
    assert recipe.steps["setup_remote"].on_success == "check_integration_exists"


def test_pmp_has_check_integration_exists_step(recipe) -> None:
    """check_integration_exists step must exist and use run_cmd."""
    assert "check_integration_exists" in recipe.steps
    assert recipe.steps["check_integration_exists"].tool == "run_cmd"


def test_pmp_check_integration_exists_cmd_uses_base_branch(recipe) -> None:
    """check_integration_exists cmd must reference inputs.base_branch."""
    cmd = recipe.steps["check_integration_exists"].with_args["cmd"]
    assert "inputs.base_branch" in cmd


def test_pmp_check_integration_exists_routes_to_analyze_prs_on_success(recipe) -> None:
    """check_integration_exists must proceed to analyze_prs when branch exists."""
    assert recipe.steps["check_integration_exists"].on_success == "analyze_prs"


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
    """create_persistent_integration must exist and use run_cmd."""
    assert "create_persistent_integration" in recipe.steps
    assert recipe.steps["create_persistent_integration"].tool == "run_cmd"


def test_pmp_create_persistent_integration_references_upstream_branch(recipe) -> None:
    """create_persistent_integration cmd must use inputs.upstream_branch as source."""
    cmd = recipe.steps["create_persistent_integration"].with_args["cmd"]
    assert "upstream_branch" in cmd


def test_pmp_create_persistent_integration_routes_to_analyze_prs(recipe) -> None:
    """After creating integration branch, pipeline must proceed to analyze_prs."""
    assert recipe.steps["create_persistent_integration"].on_success == "analyze_prs"


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


def test_ci_watch_pr_uses_integration_branch(recipe) -> None:
    """ci_watch_pr must use context.integration_branch as the branch parameter."""
    step = recipe.steps["ci_watch_pr"]
    assert "context.integration_branch" in step.with_args["branch"]


def test_ci_watch_pr_routing(recipe) -> None:
    """ci_watch_pr on_success -> confirm_cleanup; on_failure -> diagnose_ci."""
    step = recipe.steps["ci_watch_pr"]
    assert step.on_success == "confirm_cleanup"
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


def test_pmp_create_review_pr_routes_to_wait_for_mergeability(recipe) -> None:
    """B1: create_review_pr.on_success must route to wait_for_review_pr_mergeability."""
    step = recipe.steps["create_review_pr"]
    assert step.on_success == "wait_for_review_pr_mergeability"


def test_pmp_has_wait_for_review_pr_mergeability_step(recipe) -> None:
    """B2: wait_for_review_pr_mergeability step must exist and use run_cmd tool."""
    assert "wait_for_review_pr_mergeability" in recipe.steps
    step = recipe.steps["wait_for_review_pr_mergeability"]
    assert step.tool == "run_cmd"


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
    """B6: check_mergeability on_result must route MERGEABLE to review_pr_integration."""
    step = recipe.steps["check_mergeability"]
    assert step.on_result is not None
    conditions = step.on_result.conditions
    mergeable_routes = [c for c in conditions if c.when and "MERGEABLE" in c.when]
    assert any(c.route == "review_pr_integration" for c in mergeable_routes)


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
    """B9: resolve_integration_conflicts must route to force_push_after_rebase."""
    step = recipe.steps["resolve_integration_conflicts"]
    # Step uses on_result conditions; the default (no-when) bare route must route to force_push
    assert step.on_result is not None
    conditions = step.on_result.conditions
    default_routes = [c for c in conditions if c.when is None]
    assert any(c.route == "force_push_after_rebase" for c in default_routes)


def test_pmp_has_force_push_after_rebase_step(recipe) -> None:
    """B10: force_push_after_rebase step must exist with run_cmd tool and --force-with-lease."""
    assert "force_push_after_rebase" in recipe.steps
    step = recipe.steps["force_push_after_rebase"]
    assert step.tool == "run_cmd"
    assert "--force-with-lease" in step.with_args.get("cmd", "")


def test_pmp_force_push_after_rebase_routes_to_wait_for_post_rebase_mergeability(recipe) -> None:
    """B23: force_push_after_rebase.on_success must route to wait_for_post_rebase_mergeability."""
    step = recipe.steps["force_push_after_rebase"]
    assert step.on_success == "wait_for_post_rebase_mergeability"


def test_pmp_has_wait_for_post_rebase_mergeability_step(recipe) -> None:
    """B24: wait_for_post_rebase_mergeability step must exist and use run_cmd tool."""
    assert "wait_for_post_rebase_mergeability" in recipe.steps
    step = recipe.steps["wait_for_post_rebase_mergeability"]
    assert step.tool == "run_cmd"


def test_pmp_wait_for_post_rebase_mergeability_routes_to_check_post_rebase(
    recipe,
) -> None:
    """B25: wait_for_post_rebase_mergeability.on_success must route to check_mergeability_post_rebase."""  # noqa: E501
    step = recipe.steps["wait_for_post_rebase_mergeability"]
    assert step.on_success == "check_mergeability_post_rebase"


def test_pmp_has_check_mergeability_post_rebase_step(recipe) -> None:
    """B11: check_mergeability_post_rebase step must exist with check_pr_mergeable tool."""
    assert "check_mergeability_post_rebase" in recipe.steps
    step = recipe.steps["check_mergeability_post_rebase"]
    assert step.tool == "check_pr_mergeable"


def test_pmp_check_mergeability_post_rebase_routes_mergeable_to_review(recipe) -> None:
    """B12: post_rebase mergeability check must route MERGEABLE to review_pr_integration."""
    step = recipe.steps["check_mergeability_post_rebase"]
    assert step.on_result is not None
    conditions = step.on_result.conditions
    mergeable_routes = [c for c in conditions if c.when and "MERGEABLE" in c.when]
    assert any(c.route == "review_pr_integration" for c in mergeable_routes)


def test_pmp_has_review_pr_integration_step(recipe) -> None:
    """B13: review_pr_integration step must exist with run_skill tool and review-pr."""
    assert "review_pr_integration" in recipe.steps
    step = recipe.steps["review_pr_integration"]
    assert step.tool == "run_skill"
    assert "review-pr" in step.with_args.get("skill_command", "")


def test_pmp_review_pr_integration_uses_integration_branch(recipe) -> None:
    """B14: review_pr_integration skill_command must reference context.integration_branch."""
    step = recipe.steps["review_pr_integration"]
    assert "context.integration_branch" in step.with_args.get("skill_command", "")


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
    """B19: resolve_review_integration.on_success must route to re_push_review_integration."""
    step = recipe.steps["resolve_review_integration"]
    assert step.on_success == "re_push_review_integration"


def test_pmp_has_re_push_review_integration_step(recipe) -> None:
    """B20: re_push_review_integration step must exist with push_to_remote tool."""
    assert "re_push_review_integration" in recipe.steps
    step = recipe.steps["re_push_review_integration"]
    assert step.tool == "push_to_remote"


def test_pmp_re_push_review_integration_uses_integration_branch(recipe) -> None:
    """B21: re_push_review_integration must pass context.integration_branch as branch arg."""
    step = recipe.steps["re_push_review_integration"]
    assert "context.integration_branch" in step.with_args.get("branch", "")


def test_pmp_re_push_review_integration_routes_to_ci_watch(recipe) -> None:
    """B22: re_push_review_integration.on_success must route to ci_watch_pr."""
    step = recipe.steps["re_push_review_integration"]
    assert step.on_success == "ci_watch_pr"
