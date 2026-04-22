"""Compound routing integration tests for the review-resolve retry loop."""

from __future__ import annotations

import pytest

from autoskillit.recipe.io import builtin_recipes_dir, load_recipe
from autoskillit.smoke_utils import check_review_loop

pytestmark = [pytest.mark.layer("recipe"), pytest.mark.small]

RECIPE_NAMES = ["implementation.yaml", "remediation.yaml", "implementation-groups.yaml"]


@pytest.mark.parametrize("recipe_name", RECIPE_NAMES)
def test_review_loop_routes_to_review_pr_after_resolve_cycle(recipe_name: str) -> None:
    """End-to-end routing test: after resolve_review -> re_push_review,
    check_review_loop must route to review_pr (not ci_watch) when
    previous verdict was changes_requested and iterations remain.

    This test calls the actual check_review_loop callable with
    previous_verdict=changes_requested, current_iteration=0 and max_iterations=3,
    then evaluates the recipe's on_result conditions against the callable's return value.
    """
    result = check_review_loop(
        pr_number="42",
        cwd="/tmp",
        current_iteration="0",
        max_iterations="3",
        previous_verdict="changes_requested",
    )

    recipe = load_recipe(builtin_recipes_dir() / recipe_name)
    step = recipe.steps["check_review_loop"]
    assert step.on_result is not None
    conditions = step.on_result.conditions

    # Simulate template interpolation
    when_expr = conditions[0].when
    for key, value in result.items():
        when_expr = when_expr.replace(f"${{{{ result.{key} }}}}", value)

    # The first condition (route to review_pr) must be satisfiable
    # when_expr becomes "true == true and false == false" — evaluate compound
    assert _eval_compound_condition(when_expr)
    assert conditions[0].route == "review_pr"


@pytest.mark.parametrize("recipe_name", RECIPE_NAMES)
def test_review_loop_routes_to_ci_watch_at_max_iterations(recipe_name: str) -> None:
    """When max_iterations is exhausted, routing must fall through to ci_watch."""
    result = check_review_loop(
        pr_number="42",
        cwd="/tmp",
        current_iteration="2",
        max_iterations="3",
        previous_verdict="changes_requested",
    )

    recipe = load_recipe(builtin_recipes_dir() / recipe_name)
    step = recipe.steps["check_review_loop"]
    assert step.on_result is not None
    conditions = step.on_result.conditions

    # Simulate template interpolation on the first (review_pr) condition
    when_expr = conditions[0].when
    for key, value in result.items():
        when_expr = when_expr.replace(f"${{{{ result.{key} }}}}", value)

    # The first condition must NOT be satisfied — falls through to default ci_watch
    # when_expr becomes "true == true and true == false" — second clause is False
    assert not _eval_compound_condition(when_expr)
    assert conditions[1].route == "ci_watch"


@pytest.mark.parametrize("recipe_name", RECIPE_NAMES)
def test_check_review_loop_review_pr_route_uses_had_blocking_and_max_exceeded(
    recipe_name: str,
) -> None:
    """The check_review_loop on_result condition for review_pr must gate on
    had_blocking AND max_exceeded."""
    recipe = load_recipe(builtin_recipes_dir() / recipe_name)
    step = recipe.steps["check_review_loop"]
    assert step.on_result is not None
    review_conditions = [c for c in step.on_result.conditions if c.route == "review_pr"]
    assert review_conditions, f"No conditional route to review_pr found in {recipe_name}"
    review_condition = review_conditions[0]
    assert "had_blocking" in review_condition.when
    assert "max_exceeded" in review_condition.when


def _eval_compound_condition(when_expr: str) -> bool:
    """Evaluate a compound condition like 'true == true and false == false'.

    Splits on ' and ', evaluates each 'lhs == rhs' pair, ANDs results.
    """
    parts = when_expr.split(" and ")
    results = []
    for part in parts:
        lhs, rhs = [s.strip() for s in part.split("==", 1)]
        results.append(lhs == rhs)
    return all(results)


@pytest.mark.parametrize("recipe_name", RECIPE_NAMES)
def test_review_loop_routes_to_ci_watch_when_no_blocking(recipe_name: str) -> None:
    """When had_blocking=false, routing proceeds to ci_watch regardless of iteration count."""
    result = check_review_loop(
        pr_number="42",
        cwd="/tmp",
        current_iteration="0",
        max_iterations="3",
        previous_verdict="approved_with_comments",
    )
    assert result["had_blocking"] == "false"
    assert result["max_exceeded"] == "false"
    recipe = load_recipe(builtin_recipes_dir() / recipe_name)
    step = recipe.steps["check_review_loop"]
    conditions = step.on_result.conditions
    review_condition = next(c for c in conditions if c.route == "review_pr")
    when_expr = review_condition.when
    for key, value in result.items():
        when_expr = when_expr.replace(f"${{{{ result.{key} }}}}", value)
    # Compound condition "false == true and false == false" must evaluate to False
    assert not _eval_compound_condition(when_expr)


@pytest.mark.parametrize("recipe_name", RECIPE_NAMES)
def test_review_loop_routes_to_review_pr_when_had_blocking_and_not_max_exceeded(
    recipe_name: str,
) -> None:
    """When had_blocking=true and max_exceeded=false, routing goes back to review_pr."""
    result = check_review_loop(
        pr_number="42",
        cwd="/tmp",
        current_iteration="0",
        max_iterations="3",
        previous_verdict="changes_requested",
    )
    assert result["had_blocking"] == "true"
    assert result["max_exceeded"] == "false"
    recipe = load_recipe(builtin_recipes_dir() / recipe_name)
    step = recipe.steps["check_review_loop"]
    conditions = step.on_result.conditions
    review_condition = next(c for c in conditions if c.route == "review_pr")
    when_expr = review_condition.when
    for key, value in result.items():
        when_expr = when_expr.replace(f"${{{{ result.{key} }}}}", value)
    # "true == true and false == false" must evaluate to True
    assert _eval_compound_condition(when_expr)
