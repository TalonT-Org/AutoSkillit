"""Structural assertions for .autoskillit/recipes/pr-merge-pipeline.yaml."""

from __future__ import annotations

from pathlib import Path

import pytest

from autoskillit.recipe.io import load_recipe

PROJECT_ROOT = Path(__file__).parent.parent.parent


@pytest.fixture(scope="module")
def recipe():
    return load_recipe(PROJECT_ROOT / ".autoskillit" / "recipes" / "pr-merge-pipeline.yaml")


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
