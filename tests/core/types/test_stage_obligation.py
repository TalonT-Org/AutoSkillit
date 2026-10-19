"""Availability and satisfaction rules for recipe-owned stage outcomes."""

from __future__ import annotations

import pytest

pytestmark = [pytest.mark.layer("core"), pytest.mark.small]


@pytest.mark.parametrize(
    ("obligation", "open_pr", "auto_merge", "available"),
    (
        ("pr_opened", False, False, False),
        ("pr_opened", False, True, False),
        ("pr_opened", True, False, True),
        ("pr_opened", True, True, True),
        ("pr_left_unmerged", False, False, False),
        ("pr_left_unmerged", False, True, False),
        ("pr_left_unmerged", True, False, True),
        ("pr_left_unmerged", True, True, False),
    ),
)
def test_unavailable_reason_covers_publication_ingredient_combinations(
    obligation: str, open_pr: bool, auto_merge: bool, available: bool
) -> None:
    from autoskillit.core import StageObligation

    reason = StageObligation(obligation).unavailable_reason(open_pr=open_pr, auto_merge=auto_merge)

    assert (reason is None) is available
    if not available:
        assert reason
        if not open_pr:
            assert "open_pr" in reason
        elif obligation == "pr_left_unmerged" and auto_merge:
            assert "auto_merge" in reason


@pytest.mark.parametrize(
    ("obligation", "pr_state", "base_matches", "satisfied"),
    (
        ("pr_opened", "OPEN", True, True),
        ("pr_opened", "OPEN", False, False),
        ("pr_opened", "MERGED", True, True),
        ("pr_opened", "MERGED", False, False),
        ("pr_opened", "CLOSED", True, False),
        ("pr_opened", "CLOSED", False, False),
        ("pr_opened", None, True, False),
        ("pr_opened", None, False, False),
        ("pr_left_unmerged", "OPEN", True, True),
        ("pr_left_unmerged", "OPEN", False, False),
        ("pr_left_unmerged", "MERGED", True, False),
        ("pr_left_unmerged", "MERGED", False, False),
        ("pr_left_unmerged", "CLOSED", True, False),
        ("pr_left_unmerged", "CLOSED", False, False),
        ("pr_left_unmerged", None, True, False),
        ("pr_left_unmerged", None, False, False),
    ),
)
def test_violation_covers_pull_request_state_and_base_combinations(
    obligation: str, pr_state: str | None, base_matches: bool, satisfied: bool
) -> None:
    from autoskillit.core import StageObligation

    reason = StageObligation(obligation).violation(
        pr_state=pr_state,
        pr_base="main" if base_matches else "release",
        base_branch="main",
    )

    assert (reason is None) is satisfied
    if not satisfied:
        assert reason
        if pr_state is None:
            assert "no pull request could be observed" in reason
        elif not base_matches:
            assert "targets" in reason
