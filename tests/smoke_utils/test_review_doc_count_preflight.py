"""Regression coverage for review-time documentation-count preflight findings."""

from __future__ import annotations

from pathlib import Path

import pytest

from autoskillit.core import DiffAnchorAuthority
from autoskillit.smoke_utils import (
    aggregate_combined_review_candidates,
    prepare_experimental_review_publication,
)
from autoskillit.smoke_utils.review._validation import _parse_doc_count_preflight_diagnostics

pytestmark = [pytest.mark.medium]


def _authority() -> DiffAnchorAuthority:
    return DiffAnchorAuthority.authoritative(
        repository="openai/autoskillit",
        pr_number=1,
        head_sha="a" * 40,
        generation_id="generation",
        right_side_lines={},
        left_side_lines={},
    )


def _drift_output() -> str:
    return "docs/skills/catalog.md:17: claims 63 skills, actual is 64\n"


def test_doc_count_preflight_parses_drift_as_a_blocking_tests_finding() -> None:
    findings = _parse_doc_count_preflight_diagnostics(_drift_output())

    assert findings == [
        {
            "file": "docs/skills/catalog.md",
            "line": 17,
            "dimension": "tests",
            "severity": "critical",
            "message": "claims 63 skills, actual is 64",
            "requires_decision": False,
        }
    ]


def test_doc_count_preflight_aggregates_without_an_inline_diff_anchor(
    tmp_path: Path,
) -> None:
    findings = _parse_doc_count_preflight_diagnostics(_drift_output())

    result = aggregate_combined_review_candidates(
        candidates=[],
        dispositions=[],
        prior_resolved_findings=[],
        anchor_authority=_authority(),
        doc_count_findings=findings,
        snapshot={"head_sha": "head", "base_sha": "base"},
        review_root=str(tmp_path),
    )

    assert result["state"] == "complete"
    assert len(result["survivors"]) == 1
    survivor = result["survivors"][0]
    assert survivor["candidate_id"]
    assert {
        key: survivor[key]
        for key in ("file", "line", "dimension", "severity", "message", "requires_decision")
    } == findings[0]
    assert result["review_level_findings"] == [survivor]


def test_doc_count_preflight_publishes_review_level_handoff_separately(
    tmp_path: Path,
) -> None:
    findings = _parse_doc_count_preflight_diagnostics(_drift_output())
    aggregation = aggregate_combined_review_candidates(
        candidates=[],
        dispositions=[],
        prior_resolved_findings=[],
        anchor_authority=_authority(),
        doc_count_findings=findings,
        snapshot={"head_sha": "head", "base_sha": "base"},
        review_root=str(tmp_path),
    )

    publication = prepare_experimental_review_publication(
        raw_ledger={"candidate_records": aggregation["survivors"]},
        survivors=aggregation["survivors"],
        review_level_findings=aggregation["review_level_findings"],
        snapshot={"head_sha": "head", "base_sha": "base"},
        annotation_generation_id="annotation",
        mode="local",
        snapshot_is_fresh=True,
        handoff_metadata={"schema_version": 2},
    )

    handoff = publication["artifacts"]["diff_context"]
    assert handoff["schema_version"] == 2
    assert handoff["context_entries"] == []
    assert len(handoff["review_level_findings"]) == 1
    finding = handoff["review_level_findings"][0]
    assert finding["candidate_id"]
    assert {key: finding[key] for key in ("path", "line", "message", "severity", "dimension")} == {
        "path": "docs/skills/catalog.md",
        "line": 17,
        "message": "claims 63 skills, actual is 64",
        "severity": "critical",
        "dimension": "tests",
    }
