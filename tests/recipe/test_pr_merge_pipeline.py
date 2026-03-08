"""Structural assertions for the bundled pr-merge-pipeline recipe."""

from __future__ import annotations

import pytest

from autoskillit.recipe.io import builtin_recipes_dir, iter_steps_with_context, load_recipe


@pytest.fixture(scope="module")
def recipe():
    return load_recipe(builtin_recipes_dir() / "pr-merge-pipeline.yaml")


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


def test_pmp_resolve_merge_conflicts_step_exists(recipe):
    assert "resolve_merge_conflicts" in recipe.steps


def test_pmp_resolve_merge_conflicts_is_run_skill(recipe):
    step = recipe.steps["resolve_merge_conflicts"]
    assert step.tool == "run_skill"


def test_pmp_resolve_merge_conflicts_calls_correct_skill(recipe):
    step = recipe.steps["resolve_merge_conflicts"]
    cmd = step.with_args.get("skill_command", "")
    assert "/autoskillit:resolve-merge-conflicts" in cmd


def test_pmp_resolve_merge_conflicts_routes_to_retry_merge(recipe):
    step = recipe.steps["resolve_merge_conflicts"]
    # on_result is used (mutually exclusive with on_success); the fallthrough
    # route (no when condition) must lead to retry_merge_after_resolution
    assert step.on_result is not None
    fallthrough_routes = [c for c in step.on_result.conditions if c.when is None]
    routes = {c.route for c in fallthrough_routes}
    assert "retry_merge_after_resolution" in routes, (
        "resolve_merge_conflicts fallthrough must route to retry_merge_after_resolution"
    )


def test_pmp_retry_merge_after_resolution_step_exists(recipe):
    assert "retry_merge_after_resolution" in recipe.steps


def test_pmp_retry_merge_after_resolution_uses_merge_worktree(recipe):
    step = recipe.steps["retry_merge_after_resolution"]
    assert step.tool == "merge_worktree"


def test_pmp_retry_merge_after_resolution_routes_to_next_part(recipe):
    step = recipe.steps["retry_merge_after_resolution"]
    assert step.on_success == "next_part_or_next_pr"


def test_pmp_retry_merge_no_loop_back_to_resolve(recipe):
    step = recipe.steps["retry_merge_after_resolution"]
    # Must not have on_result routing back to resolve_merge_conflicts — prevents infinite loop
    if step.on_result is not None:
        for condition in step.on_result.conditions:
            assert condition.route != "resolve_merge_conflicts", (
                "retry_merge_after_resolution must never route back to resolve_merge_conflicts"
            )
