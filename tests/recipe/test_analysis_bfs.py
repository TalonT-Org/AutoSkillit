"""Tests for bfs_reachable_without_barrier API and the all_paths_cross
dominator helper."""

from __future__ import annotations

import pytest

from autoskillit.recipe._analysis_bfs import (
    all_paths_cross,
    bfs_reachable_without_barrier,
)
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


# ---------------------------------------------------------------------------
# all_paths_cross — dominator helper (Step 1)
# ---------------------------------------------------------------------------


class TestAllPathsCross:
    """all_paths_cross returns True iff every path from start to target crosses candidate.

    Used to upgrade existential 'is X reachable?' checks into universal
    'is X on every path?' (dominator) checks. The vacuous-true guard prevents
    unreachable targets from being treated as dominated.
    """

    def test_linear_path_candidate_on_path(self) -> None:
        """Linear A → B → C → D: B is on every path from A to D."""
        graph: dict[str, set[str]] = {
            "A": {"B"},
            "B": {"C"},
            "C": {"D"},
            "D": set(),
        }
        assert all_paths_cross(graph, "A", "B", "D") is True

    def test_forking_path_with_dominator(self) -> None:
        """A → B → C / A → B → D, C → E, D → E: B dominates E from A."""
        graph: dict[str, set[str]] = {
            "A": {"B"},
            "B": {"C", "D"},
            "C": {"E"},
            "D": {"E"},
            "E": set(),
        }
        assert all_paths_cross(graph, "A", "B", "E") is True

    def test_forking_path_without_dominator(self) -> None:
        """A → B / A → C, B → D, C → D: D is reachable but B does not dominate."""
        graph: dict[str, set[str]] = {
            "A": {"B", "C"},
            "B": {"D"},
            "C": {"D"},
            "D": set(),
        }
        # A → C → D skips B entirely
        assert all_paths_cross(graph, "A", "B", "D") is False

    def test_candidate_is_target(self) -> None:
        """A node trivially dominates itself (target == candidate)."""
        graph: dict[str, set[str]] = {
            "A": {"B"},
            "B": {"C"},
            "C": set(),
        }
        assert all_paths_cross(graph, "A", "B", "B") is True

    def test_target_unreachable_returns_false(self) -> None:
        """Vacuous-true guard: unreachable target must NOT report dominance."""
        graph: dict[str, set[str]] = {
            "A": {"B"},
            "B": set(),
        }
        # X is not in the graph; cannot be reached; must NOT be vacuously dominated
        assert all_paths_cross(graph, "A", "B", "X") is False

    def test_target_not_reachable_from_start(self) -> None:
        """Start and target are in the graph but disconnected."""
        graph: dict[str, set[str]] = {
            "A": {"B"},
            "B": set(),
            "C": {"D"},
            "D": set(),
        }
        assert all_paths_cross(graph, "A", "B", "D") is False

    def test_candidate_after_target_returns_false(self) -> None:
        """Candidate is downstream of target — not on any path from start to target."""
        graph: dict[str, set[str]] = {
            "A": {"B"},
            "B": set(),
            "C": {"A", "B"},  # C is upstream, irrelevant
        }
        # Target is B; candidate is C — every path from A to B never crosses C
        # (C is reachable from B but not the other way around).
        assert all_paths_cross(graph, "A", "C", "B") is False

    def test_barrier_respects_edge_types(self) -> None:
        """The graph argument is the caller's chosen adjacency — on_failure edges
        not included in the graph are not traversable by all_paths_cross.

        Layout: A → B (success), A -fail-> C (failure).
        When the graph only contains A → B (success-path graph), C is not
        reachable and the target C is therefore unreachable from A.
        """
        success_graph: dict[str, set[str]] = {
            "A": {"B"},
            "B": set(),
        }
        # C exists in the recipe but is NOT in the success-graph
        assert all_paths_cross(success_graph, "A", "B", "C") is False
