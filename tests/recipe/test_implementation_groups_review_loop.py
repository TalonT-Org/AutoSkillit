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
def test_check_review_loop_on_failure_routes_to_ci_watch(recipe) -> None:
    """check_review_loop on_failure must route to ci_watch in implementation-groups recipe."""
    step = recipe.steps["check_review_loop"]
    assert step.on_failure == "ci_watch"
