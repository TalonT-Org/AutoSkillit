"""Tests for bfs_reachable_without_barrier API — frozenset barrier support (T-B-RI1)."""

from __future__ import annotations

import pytest

from autoskillit.recipe._analysis_bfs import bfs_reachable_without_barrier
from autoskillit.recipe.io import builtin_recipes_dir, load_recipe

pytestmark = [pytest.mark.layer("recipe"), pytest.mark.small]


def test_bfs_accepts_frozenset_barrier():
    """T-B-RI1: bfs_reachable_without_barrier accepts a frozenset barrier."""
    recipe = load_recipe(builtin_recipes_dir() / "implementation.yaml")
    result = bfs_reachable_without_barrier(
        recipe,
        start="review_pr",
        barrier=frozenset({"check_review_loop", "check_review_posted"}),
    )
    assert isinstance(result, set)


def test_bfs_accepts_string_barrier_unchanged():
    """T-B-RI1: bfs_reachable_without_barrier still accepts a plain string barrier."""
    recipe = load_recipe(builtin_recipes_dir() / "implementation.yaml")
    result = bfs_reachable_without_barrier(
        recipe,
        start="review_pr",
        barrier="check_review_loop",
    )
    assert isinstance(result, set)


def test_bfs_frozenset_barrier_blocks_all_named_steps():
    """With frozenset barrier, none of the listed steps are expanded."""
    recipe = load_recipe(builtin_recipes_dir() / "implementation.yaml")
    barriers = frozenset({"check_review_posted", "check_review_loop"})
    result = bfs_reachable_without_barrier(
        recipe,
        start="review_pr",
        barrier=barriers,
    )
    # check_repo_ci_event is only reachable through check_review_loop — must not appear
    # because check_review_loop is a barrier and is not expanded
    assert "check_repo_ci_event" not in result


def test_bfs_single_barrier_string_matches_frozenset_singleton():
    """String barrier and singleton frozenset produce same reachability result."""
    recipe = load_recipe(builtin_recipes_dir() / "implementation.yaml")
    result_str = bfs_reachable_without_barrier(
        recipe,
        start="review_pr",
        barrier="check_review_loop",
    )
    result_set = bfs_reachable_without_barrier(
        recipe,
        start="review_pr",
        barrier=frozenset({"check_review_loop"}),
    )
    assert result_str == result_set
