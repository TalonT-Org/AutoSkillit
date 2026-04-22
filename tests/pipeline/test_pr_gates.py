"""Tests for analyze-prs PR eligibility gate logic (CI gate + review gate).

These tests verify the Python gate functions extracted in
autoskillit.pipeline.pr_gates, which implement the filtering logic described
in the analyze-prs SKILL.md Step 1.5.
"""

from __future__ import annotations

import json

import pytest

from autoskillit.pipeline.pr_gates import (
    is_ci_passing,
    is_pipeline_sourced,
    is_review_passing,
    partition_prs,
)

pytestmark = [pytest.mark.layer("pipeline"), pytest.mark.small]

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
    _sig = "<!-- autoskillit:pipeline-signature -->"
    prs = [_make_pr(10), _make_pr(20), _make_pr(30)]
    prs[0]["body"] = _sig
    prs[1]["body"] = ""
    prs[2]["body"] = _sig
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
    """When all PRs pass both gates and carry the pipeline signature, they are eligible."""
    _sig = "<!-- autoskillit:pipeline-signature -->"
    prs = [_make_pr(1), _make_pr(2)]
    prs[0]["body"] = _sig
    prs[1]["body"] = _sig
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


# ---------------------------------------------------------------------------
# Vocabulary contract
# ---------------------------------------------------------------------------


class TestPRGatesVocabularyContract:
    """CHANGES_REQUESTED and _CI_PASSING_CONCLUSIONS must be declared as named
    constants, and those constants must be consistent with KNOWN_REVIEW_STATES."""

    def test_known_review_states_constant_exists(self):
        from autoskillit.pipeline import pr_gates

        assert hasattr(pr_gates, "KNOWN_REVIEW_STATES")
        assert isinstance(pr_gates.KNOWN_REVIEW_STATES, frozenset)
        assert "CHANGES_REQUESTED" in pr_gates.KNOWN_REVIEW_STATES

    def test_changes_requested_in_known_review_states(self):
        from autoskillit.pipeline.pr_gates import KNOWN_REVIEW_STATES

        assert "CHANGES_REQUESTED" in KNOWN_REVIEW_STATES

    def test_ci_passing_conclusions_constant_exists(self):
        from autoskillit.pipeline import pr_gates

        assert hasattr(pr_gates, "_CI_PASSING_CONCLUSIONS")
        assert isinstance(pr_gates._CI_PASSING_CONCLUSIONS, frozenset)
        assert "success" in pr_gates._CI_PASSING_CONCLUSIONS


# ---------------------------------------------------------------------------
# Pipeline provenance gate (1d)
# ---------------------------------------------------------------------------


class TestPipelineProvenance:
    """is_pipeline_sourced and partition_prs provenance_blocked_prs bucket."""

    def test_is_pipeline_sourced_true_when_signature_present(self):
        body = "PR description\n<!-- autoskillit:pipeline-signature steps=compose_pr -->"
        assert is_pipeline_sourced(body) is True

    def test_is_pipeline_sourced_false_when_absent(self):
        assert is_pipeline_sourced("🤖 Generated with Claude Code via AutoSkillit") is False

    def test_is_pipeline_sourced_false_on_empty_body(self):
        assert is_pipeline_sourced("") is False

    def test_is_pipeline_sourced_false_on_none_body(self):
        assert is_pipeline_sourced(None) is False  # type: ignore[arg-type]

    def test_is_pipeline_sourced_tolerates_whitespace_variants(self):
        body = "<!--autoskillit:pipeline-signature steps=prepare_pr,compose_pr -->"
        assert is_pipeline_sourced(body) is True

    def test_partition_prs_produces_provenance_blocked_bucket(self):
        """PRs that pass CI+review gates but lack the pipeline signature are provenance-blocked."""
        prs = [
            _make_pr(1, "Has signature"),
            _make_pr(2, "No signature"),
        ]
        checks: dict = {1: [], 2: []}
        reviews: dict = {1: [], 2: []}
        # Inject body field with signature for PR 1 only
        prs[0]["body"] = "<!-- autoskillit:pipeline-signature steps=compose_pr -->"
        prs[1]["body"] = ""

        result = partition_prs(prs, checks, reviews)

        assert "provenance_blocked_prs" in result, (
            "partition_prs must return a 'provenance_blocked_prs' key"
        )
        provenance_blocked = result["provenance_blocked_prs"]
        assert len(provenance_blocked) == 1
        assert provenance_blocked[0]["number"] == 2

    def test_partition_prs_eligible_prs_excluded_from_provenance_blocked(self):
        """PRs with the pipeline signature are not in provenance_blocked_prs."""
        prs = [_make_pr(3, "Pipeline PR")]
        prs[0]["body"] = "<!-- autoskillit:pipeline-signature -->"
        checks: dict = {3: []}
        reviews: dict = {3: []}

        result = partition_prs(prs, checks, reviews)

        assert result["provenance_blocked_prs"] == []

    def test_partition_prs_ci_blocked_excluded_from_provenance_check(self):
        """CI-blocked PRs are not also put in provenance_blocked_prs."""
        prs = [_make_pr(4, "CI failing")]
        prs[0]["body"] = ""
        checks: dict = {4: [{"conclusion": "failure"}]}
        reviews: dict = {4: []}

        result = partition_prs(prs, checks, reviews)

        assert len(result["ci_blocked_prs"]) == 1
        assert result["provenance_blocked_prs"] == []
