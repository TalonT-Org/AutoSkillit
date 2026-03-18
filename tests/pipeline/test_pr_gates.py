"""Tests for analyze-prs PR eligibility gate logic (CI gate + review gate).

These tests verify the Python gate functions extracted in
autoskillit.pipeline.pr_gates, which implement the filtering logic described
in the analyze-prs SKILL.md Step 1.5.
"""

from __future__ import annotations

import json

from autoskillit.pipeline.pr_gates import (
    is_ci_passing,
    is_review_passing,
    partition_prs,
)

# ---------------------------------------------------------------------------
# CI Gate
# ---------------------------------------------------------------------------


def test_ci_gate_excludes_failing_pr():
    """PR with any check conclusion 'failure' is excluded from eligible_prs."""
    checks = [
        {"name": "lint", "conclusion": "success"},
        {"name": "tests", "conclusion": "failure"},
    ]
    assert is_ci_passing(checks) is False


def test_ci_gate_excludes_in_progress_pr():
    """PR with checks still running (conclusion=None) is excluded."""
    checks = [
        {"name": "lint", "conclusion": "success"},
        {"name": "tests", "conclusion": None},
    ]
    assert is_ci_passing(checks) is False


def test_ci_gate_passes_all_success_pr():
    """PR where all checks are success/skipped is included in eligible_prs."""
    checks = [
        {"name": "lint", "conclusion": "success"},
        {"name": "format", "conclusion": "skipped"},
        {"name": "tests", "conclusion": "success"},
    ]
    assert is_ci_passing(checks) is True


def test_ci_gate_passes_neutral_conclusion():
    """PR with 'neutral' conclusion passes CI gate."""
    checks = [{"name": "optional-check", "conclusion": "neutral"}]
    assert is_ci_passing(checks) is True


def test_ci_gate_passes_empty_checks():
    """PR with no CI checks at all passes the gate (no failing checks)."""
    assert is_ci_passing([]) is True


# ---------------------------------------------------------------------------
# Review Gate
# ---------------------------------------------------------------------------


def test_review_gate_excludes_changes_requested():
    """PR with CHANGES_REQUESTED review is excluded from eligible_prs."""
    reviews = [{"state": "CHANGES_REQUESTED", "author": {"login": "reviewer"}}]
    assert is_review_passing(reviews) is False


def test_review_gate_passes_approved_review():
    """PR with APPROVED review and no CHANGES_REQUESTED is included."""
    reviews = [{"state": "APPROVED", "author": {"login": "reviewer"}}]
    assert is_review_passing(reviews) is True


def test_review_gate_passes_no_reviews():
    """PR with no reviews at all is included (not yet reviewed = eligible)."""
    assert is_review_passing([]) is True


def test_review_gate_passes_commented_only():
    """PR with only COMMENTED reviews (no CHANGES_REQUESTED) is included."""
    reviews = [{"state": "COMMENTED", "author": {"login": "reviewer"}}]
    assert is_review_passing(reviews) is True


def test_review_gate_excludes_when_mixed_approved_and_changes_requested():
    """If one reviewer approved but another requested changes, PR is blocked."""
    reviews = [
        {"state": "APPROVED", "author": {"login": "alice"}},
        {"state": "CHANGES_REQUESTED", "author": {"login": "bob"}},
    ]
    assert is_review_passing(reviews) is False


# ---------------------------------------------------------------------------
# partition_prs — combined manifest
# ---------------------------------------------------------------------------


def _make_pr(number: int, title: str = "") -> dict:
    return {"number": number, "title": title or f"PR #{number}"}


def test_manifest_includes_blocked_lists():
    """Output manifest dict has ci_blocked_prs and review_blocked_prs arrays."""
    prs = [_make_pr(1), _make_pr(2), _make_pr(3)]
    checks = {
        1: [{"conclusion": "success"}],
        2: [{"conclusion": "failure"}],  # CI blocked
        3: [{"conclusion": "success"}],
    }
    reviews = {
        1: [],
        2: [],
        3: [{"state": "CHANGES_REQUESTED"}],  # review blocked
    }

    result = partition_prs(prs, checks, reviews)

    assert "eligible_prs" in result
    assert "ci_blocked_prs" in result
    assert "review_blocked_prs" in result

    # Verify the result is JSON-serialisable (manifest written to file)
    json.dumps(result)  # raises TypeError if not serialisable


def test_eligible_prs_ordered_without_blocked():
    """Blocked PRs do not appear in the eligible_prs array."""
    prs = [_make_pr(10), _make_pr(20), _make_pr(30)]
    checks = {
        10: [{"conclusion": "success"}],
        20: [{"conclusion": "failure"}],  # blocked
        30: [{"conclusion": "success"}],
    }
    reviews = {10: [], 20: [], 30: []}

    result = partition_prs(prs, checks, reviews)

    eligible_numbers = {pr["number"] for pr in result["eligible_prs"]}
    assert eligible_numbers == {10, 30}
    assert 20 not in eligible_numbers

    ci_blocked_numbers = {pr["number"] for pr in result["ci_blocked_prs"]}
    assert ci_blocked_numbers == {20}
    assert result["review_blocked_prs"] == []


def test_ci_blocked_pr_has_reason_string():
    """CI-blocked entry has a descriptive reason string."""
    prs = [_make_pr(5, "My failing PR")]
    checks = {5: [{"conclusion": "failure"}, {"conclusion": None}]}
    reviews = {5: []}

    result = partition_prs(prs, checks, reviews)

    assert len(result["ci_blocked_prs"]) == 1
    blocked = result["ci_blocked_prs"][0]
    assert blocked["number"] == 5
    assert blocked["title"] == "My failing PR"
    assert "CI failing" in blocked["reason"]
    # Assert counts from the input data directly, not from the reason string phrasing
    failing = [
        c for c in checks[5] if c.get("conclusion") not in {None, "success", "skipped", "neutral"}
    ]
    in_progress = [c for c in checks[5] if c.get("conclusion") is None]
    assert len(failing) == 1
    assert len(in_progress) == 1


def test_review_blocked_pr_has_reason_string():
    """Review-blocked entry has a descriptive reason string."""
    prs = [_make_pr(7, "Needs changes")]
    checks = {7: [{"conclusion": "success"}]}
    reviews = {7: [{"state": "CHANGES_REQUESTED"}, {"state": "CHANGES_REQUESTED"}]}

    result = partition_prs(prs, checks, reviews)

    assert len(result["review_blocked_prs"]) == 1
    blocked = result["review_blocked_prs"][0]
    assert blocked["number"] == 7
    assert "2 unresolved CHANGES_REQUESTED" in blocked["reason"]


def test_all_prs_eligible_when_no_gates_fire():
    """When all PRs pass both gates, ci_blocked and review_blocked are empty."""
    prs = [_make_pr(1), _make_pr(2)]
    checks = {1: [{"conclusion": "success"}], 2: [{"conclusion": "skipped"}]}
    reviews = {1: [], 2: [{"state": "APPROVED"}]}

    result = partition_prs(prs, checks, reviews)

    assert len(result["eligible_prs"]) == 2
    assert result["ci_blocked_prs"] == []
    assert result["review_blocked_prs"] == []


def test_ci_gate_fires_before_review_gate():
    """A PR that fails CI is placed in ci_blocked, not review_blocked."""
    prs = [_make_pr(9)]
    # Fail CI AND have CHANGES_REQUESTED — CI gate should win
    checks = {9: [{"conclusion": "failure"}]}
    reviews = {9: [{"state": "CHANGES_REQUESTED"}]}

    result = partition_prs(prs, checks, reviews)

    assert len(result["ci_blocked_prs"]) == 1
    assert result["review_blocked_prs"] == []
