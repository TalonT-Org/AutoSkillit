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
    iterations remain.

    This test calls the actual check_review_loop callable with
    current_iteration=0 and max_iterations=3, then evaluates the
    recipe's on_result conditions against the callable's return value.
    """
    result = check_review_loop(
        pr_number="42",
        cwd="/tmp",
        current_iteration="0",
        max_iterations="3",
    )

    recipe = load_recipe(builtin_recipes_dir() / recipe_name)
    step = recipe.steps["check_review_loop"]
    conditions = step.on_result.conditions

    # Simulate template interpolation
    when_expr = conditions[0].when
    for key, value in result.items():
        when_expr = when_expr.replace(f"${{{{ result.{key} }}}}", value)

    # The first condition (route to review_pr) must be satisfiable
    # when_expr becomes e.g. "false == false" — evaluate as string equality
    lhs, rhs = [s.strip() for s in when_expr.split("==")]
    assert lhs == rhs
    assert conditions[0].route == "review_pr"


@pytest.mark.parametrize("recipe_name", RECIPE_NAMES)
def test_review_loop_routes_to_ci_watch_at_max_iterations(recipe_name: str) -> None:
    """When max_iterations is exhausted, routing must fall through to ci_watch."""
    result = check_review_loop(
        pr_number="42",
        cwd="/tmp",
        current_iteration="2",
        max_iterations="3",
    )

    recipe = load_recipe(builtin_recipes_dir() / recipe_name)
    step = recipe.steps["check_review_loop"]
    conditions = step.on_result.conditions

    # Simulate template interpolation on the first (review_pr) condition
    when_expr = conditions[0].when
    for key, value in result.items():
        when_expr = when_expr.replace(f"${{{{ result.{key} }}}}", value)

    # The first condition must NOT be satisfied — falls through to default ci_watch
    # when_expr becomes e.g. "true == false" — evaluate as string equality
    lhs, rhs = [s.strip() for s in when_expr.split("==")]
    assert lhs != rhs


@pytest.mark.parametrize("recipe_name", RECIPE_NAMES)
def test_check_review_loop_routes_on_max_exceeded_only(recipe_name: str) -> None:
    """The check_review_loop on_result condition must route on max_exceeded alone,
    NOT on has_blocking."""
    recipe = load_recipe(builtin_recipes_dir() / recipe_name)
    step = recipe.steps["check_review_loop"]
    conditions = step.on_result.conditions
    review_condition = next(c for c in conditions if c.route == "review_pr")
    assert "max_exceeded" in review_condition.when
    assert "has_blocking" not in review_condition.when
