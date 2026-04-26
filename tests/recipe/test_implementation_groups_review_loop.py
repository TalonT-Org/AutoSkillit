"""Tests for review-resolve retry loop steps in implementation-groups.yaml
(T_IG_LOOP1–T_IG_LOOP3)."""

from __future__ import annotations

import pytest

from autoskillit.recipe.io import builtin_recipes_dir, load_recipe

pytestmark = [pytest.mark.layer("recipe"), pytest.mark.small]

RECIPE_PATH = builtin_recipes_dir() / "implementation-groups.yaml"


@pytest.fixture(scope="module")
def recipe():
    return load_recipe(RECIPE_PATH)


# T_IG_LOOP1
def test_check_review_loop_step_exists(recipe) -> None:
    """check_review_loop step must exist in implementation-groups recipe."""
    assert "check_review_loop" in recipe.steps


# T_IG_LOOP2
def test_re_push_review_routes_to_check_review_loop(recipe) -> None:
    """re_push_review on_success must route to check_review_loop in implementation-groups
    recipe."""
    step = recipe.steps["re_push_review"]
    assert step.on_success == "check_review_loop"


# T_IG_LOOP3
def test_check_review_loop_on_result_routes_to_review_pr_when_had_blocking_and_not_max_exceeded(
    recipe,
) -> None:
    """check_review_loop on_result condition must gate on had_blocking AND max_exceeded."""
    step = recipe.steps["check_review_loop"]
    assert step.on_result is not None
    review_conditions = [
        c for c in step.on_result.conditions if c.when is not None and c.route == "review_pr"
    ]
    assert review_conditions, "No conditional route to review_pr found"
    cond = review_conditions[0].when
    assert "had_blocking" in cond
    assert "max_exceeded" in cond


# T_IG_LOOP4
def test_check_review_loop_has_on_failure(recipe) -> None:
    """check_review_loop must declare on_failure because it uses on_result
    (on-result-missing-failure-route semantic rule requires it)."""
    step = recipe.steps["check_review_loop"]
    assert step.on_failure == "check_repo_ci_event"


# T_IG_LOOP5
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


# T_IG_LOOP6
def test_review_pr_captures_review_verdict(recipe) -> None:
    """review_pr must capture verdict as review_verdict (not verdict) to avoid clobber."""
    step = recipe.steps["review_pr"]
    capture = step.capture or {}
    assert "review_verdict" in capture
    assert "result.verdict" in capture["review_verdict"]


# T_IG_LOOP7
def test_check_review_loop_with_args_has_previous_verdict(recipe) -> None:
    """check_review_loop with: must pass previous_verdict from context.review_verdict."""
    step = recipe.steps["check_review_loop"]
    assert "previous_verdict" in step.with_args
    assert "review_verdict" in step.with_args["previous_verdict"]
