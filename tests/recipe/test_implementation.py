"""Tests for review-resolve retry loop steps in implementation.yaml (T_IP_LOOP1–T_IP_LOOP10)."""

from __future__ import annotations

import pytest

from autoskillit.recipe.io import builtin_recipes_dir, load_recipe

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


# T_IP_LOOP3
def test_check_review_loop_has_skip_when_false_open_pr(recipe) -> None:
    """check_review_loop must be skipped when inputs.open_pr is false."""
    step = recipe.steps["check_review_loop"]
    assert step.skip_when_false == "inputs.open_pr"


# T_IP_LOOP4
def test_check_review_loop_on_result_routes_to_review_pr_when_had_blocking_and_not_max_exceeded(
    recipe,
) -> None:
    """check_review_loop on_result routes to review_pr only when
    had_blocking=true AND max_exceeded=false."""
    step = recipe.steps["check_review_loop"]
    assert step.on_result is not None
    review_conditions = [
        c for c in step.on_result.conditions if c.when is not None and c.route == "review_pr"
    ]
    assert review_conditions, "No conditional route to review_pr found"
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
def test_re_push_review_routes_to_check_review_loop(recipe) -> None:
    """re_push_review on_success must route to check_review_loop, not ci_watch directly."""
    step = recipe.steps["re_push_review"]
    assert step.on_success == "check_review_loop"


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
    assert "next_iteration" in capture["review_loop_count"]


# T_IP_LOOP11
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


# T_IP_LOOP12
def test_review_pr_captures_review_verdict(recipe) -> None:
    """review_pr must capture verdict as review_verdict (not verdict) to avoid clobber."""
    step = recipe.steps["review_pr"]
    capture = step.capture or {}
    assert "review_verdict" in capture
    assert "result.verdict" in capture["review_verdict"]


# T_IP_LOOP13
def test_check_review_loop_with_args_has_previous_verdict(recipe) -> None:
    """check_review_loop with: must pass previous_verdict from context.review_verdict."""
    step = recipe.steps["check_review_loop"]
    assert "previous_verdict" in step.with_args
    assert "review_verdict" in step.with_args["previous_verdict"]


def test_capture_base_sha_captures_both_base_sha_and_merge_target(recipe) -> None:
    """capture_base_sha must capture both base_sha and merge_target in a single step."""
    step = recipe.steps["capture_base_sha"]
    capture = step.capture or {}
    assert "base_sha" in capture, (
        "capture_base_sha must capture base_sha — the SHA of base_branch before any merge"
    )
    assert "merge_target" in capture, (
        "capture_base_sha must capture merge_target — the fallback target branch name "
        "(replaces the removed set_merge_target step)"
    )
