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
def test_check_review_loop_routes_on_max_exceeded_only(recipe) -> None:
    """check_review_loop on_result condition must route on max_exceeded alone,
    NOT on has_blocking."""
    step = recipe.steps["check_review_loop"]
    assert step.on_result is not None
    review_conditions = [
        c for c in step.on_result.conditions if c.when is not None and c.route == "review_pr"
    ]
    assert review_conditions, "No conditional route to review_pr found"
    assert "max_exceeded" in review_conditions[0].when
    assert "has_blocking" not in review_conditions[0].when


# T_IG_LOOP4
def test_check_review_loop_has_on_failure(recipe) -> None:
    """check_review_loop must declare on_failure because it uses on_result
    (on-result-missing-failure-route semantic rule requires it)."""
    step = recipe.steps["check_review_loop"]
    assert step.on_failure == "ci_watch"
