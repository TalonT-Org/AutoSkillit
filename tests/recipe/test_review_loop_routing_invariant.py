"""Routing invariant tests for the review loop waypoint.

Invariant: check_review_loop is a mandatory waypoint between review_pr and
check_repo_ci_event. No verdict may route directly from review_pr to
check_repo_ci_event without first crossing check_review_loop.

These tests verify the structural routing graph of the three recipes that
contain both review_pr and check_review_loop: implementation, remediation,
and implementation-groups.
"""

from __future__ import annotations

import pytest

from autoskillit.recipe.io import builtin_recipes_dir, load_recipe

pytestmark = [pytest.mark.layer("recipe"), pytest.mark.small]

REVIEW_LOOP_RECIPES = ["implementation", "remediation", "implementation-groups"]


@pytest.mark.parametrize("recipe_name", REVIEW_LOOP_RECIPES)
def test_approved_verdict_must_not_route_directly_to_ci(recipe_name: str) -> None:
    """The catch-all on_result may not route directly to check_repo_ci_event.

    All verdicts must pass through check_review_loop so that review_loop_count
    is always incremented before exiting to CI.
    """
    recipe = load_recipe(builtin_recipes_dir() / f"{recipe_name}.yaml")
    step = recipe.steps["review_pr"]
    direct_routes = {c.route for c in (step.on_result.conditions if step.on_result else [])}
    assert "check_repo_ci_event" not in direct_routes, (
        f"[{recipe_name}] review_pr routes directly to check_repo_ci_event, "
        f"bypassing check_review_loop. All verdicts must pass through check_review_loop."
    )


@pytest.mark.parametrize("recipe_name", REVIEW_LOOP_RECIPES)
def test_check_repo_ci_event_not_bfs_reachable_without_barrier(recipe_name: str) -> None:
    """BFS from review_pr must not reach check_repo_ci_event without check_review_loop.

    The barrier pattern ensures that every path to check_repo_ci_event first
    crosses check_review_loop, guaranteeing counter graduation.
    """
    from autoskillit.recipe._analysis_bfs import bfs_reachable_without_barrier

    recipe = load_recipe(builtin_recipes_dir() / f"{recipe_name}.yaml")
    reachable = bfs_reachable_without_barrier(
        recipe=recipe, start="review_pr", barrier="check_review_loop"
    )
    assert "check_repo_ci_event" not in reachable, (
        f"[{recipe_name}] check_repo_ci_event BFS-reachable from review_pr "
        f"without crossing check_review_loop. Counter graduation is bypassed."
    )


@pytest.mark.parametrize("recipe_name", REVIEW_LOOP_RECIPES)
def test_check_review_loop_step_receives_local_review_rounds(recipe_name: str) -> None:
    """The check_review_loop step must receive local_review_rounds in with_args.

    Without this ingredient, the callable cannot compute had_blocking correctly
    for the local_review_rounds graduation policy.
    """
    recipe = load_recipe(builtin_recipes_dir() / f"{recipe_name}.yaml")
    step = recipe.steps["check_review_loop"]
    assert "local_review_rounds" in step.with_args, (
        f"[{recipe_name}] check_review_loop step missing local_review_rounds in with_args."
    )


@pytest.mark.parametrize("recipe_name", REVIEW_LOOP_RECIPES)
def test_review_pr_not_bfs_reachable_from_check_review_loop_without_annotate(
    recipe_name: str,
) -> None:
    """BFS from check_review_loop must not reach review_pr without annotate_pr_diff.

    The re-entry barrier ensures that every loop-back path from check_review_loop
    to review_pr first crosses annotate_pr_diff, so review_mode is recomputed
    with the updated review_loop_count on every iteration.
    """
    from autoskillit.recipe._analysis_bfs import bfs_reachable_without_barrier

    recipe = load_recipe(builtin_recipes_dir() / f"{recipe_name}.yaml")
    reachable = bfs_reachable_without_barrier(
        recipe=recipe, start="check_review_loop", barrier="annotate_pr_diff"
    )
    assert "review_pr" not in reachable, (
        f"[{recipe_name}] review_pr BFS-reachable from check_review_loop "
        f"without crossing annotate_pr_diff. review_mode is never recomputed "
        f"on loop re-entry — mode graduation from local to github is broken."
    )
