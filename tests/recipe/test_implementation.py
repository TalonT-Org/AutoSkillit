"""Tests for review-resolve retry loop steps in implementation.yaml (T_IP_LOOP1–T_IP_LOOP10)."""

from __future__ import annotations

import pytest

from autoskillit.recipe.io import builtin_recipes_dir, load_recipe
from autoskillit.recipe.validator import validate_recipe

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
def test_check_review_loop_on_result_routes_to_review_pr_when_blocking(recipe) -> None:
    """check_review_loop on_result routes to review_pr when has_blocking=true AND max_exceeded=false."""
    step = recipe.steps["check_review_loop"]
    assert step.on_result is not None
    blocking_conditions = [
        c for c in step.on_result.conditions
        if c.when is not None and "has_blocking" in c.when and "max_exceeded" in c.when
    ]
    assert blocking_conditions, "No condition for has_blocking + max_exceeded found"
    assert blocking_conditions[0].route == "review_pr"


# T_IP_LOOP5
def test_check_review_loop_on_result_default_routes_to_ci_watch(recipe) -> None:
    """check_review_loop on_result routes to ci_watch as default (no blocking or max exceeded)."""
    step = recipe.steps["check_review_loop"]
    assert step.on_result is not None
    default_conditions = [c for c in step.on_result.conditions if c.when is None]
    assert default_conditions, "No default (when=None) condition found"
    assert default_conditions[0].route == "ci_watch"


# T_IP_LOOP6
def test_check_review_loop_on_failure_routes_to_ci_watch(recipe) -> None:
    """check_review_loop on_failure routes to ci_watch (API failure must not block pipeline)."""
    step = recipe.steps["check_review_loop"]
    assert step.on_failure == "ci_watch"


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
