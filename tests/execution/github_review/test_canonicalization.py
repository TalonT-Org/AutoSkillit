"""Deterministic canonical identity for GitHub review operations."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from autoskillit.core import GitHubReviewComment, GitHubReviewRequest
from autoskillit.execution import (
    canonicalize_review_request,
    compute_review_operation_key,
)

pytestmark = [pytest.mark.layer("execution"), pytest.mark.small]


def _request(**overrides: object) -> GitHubReviewRequest:
    values: dict[str, object] = {
        "repository": "octo/example",
        "pr_number": 42,
        "head_sha": "a" * 40,
        "logical_iteration": "review-pr:2",
        "event": "COMMENT",
        "body": "Review body",
        "comments": (
            GitHubReviewComment(
                path="src/a.py",
                line=10,
                body="First",
                start_line=8,
                start_side="RIGHT",
            ),
            GitHubReviewComment(path="src/b.py", line=20, body="Second"),
        ),
    }
    values.update(overrides)
    return GitHubReviewRequest(**values)


def test_canonicalization_is_stable_utf8_json_with_all_remote_identity_fields() -> None:
    canonical = canonicalize_review_request(_request())
    assert isinstance(canonical, bytes)
    assert canonical == canonicalize_review_request(_request())
    assert canonical.endswith(b"}")
    decoded = json.loads(canonical)
    assert decoded == {
        "body": "Review body",
        "comments": [
            {
                "body": "First",
                "line": 10,
                "path": "src/a.py",
                "side": "RIGHT",
                "start_line": 8,
                "start_side": "RIGHT",
            },
            {
                "body": "Second",
                "line": 20,
                "path": "src/b.py",
                "side": "RIGHT",
                "start_line": None,
                "start_side": None,
            },
        ],
        "event": "COMMENT",
        "head_sha": "a" * 40,
        "logical_iteration": "review-pr:2",
        "pr_number": 42,
        "repository": "octo/example",
        "schema_version": 1,
    }


def test_local_delivery_options_do_not_change_remote_operation_identity(
    tmp_path: Path,
) -> None:
    normal = _request()
    local_variant = _request(
        dry_run=True,
        receipt_path=tmp_path / "another" / "receipt.json",
    )
    assert canonicalize_review_request(normal) == canonicalize_review_request(local_variant)
    assert compute_review_operation_key(normal) == compute_review_operation_key(local_variant)


@pytest.mark.parametrize(
    "changed",
    [
        _request(repository="octo/other"),
        _request(pr_number=43),
        _request(head_sha="b" * 40),
        _request(logical_iteration="review-pr:3"),
        _request(comments=(GitHubReviewComment(path="src/a.py", line=10, body="Changed"),)),
    ],
)
def test_every_remote_semantic_change_changes_the_operation_key(
    changed: GitHubReviewRequest,
) -> None:
    baseline_key = compute_review_operation_key(_request())
    changed_key = compute_review_operation_key(changed)
    assert changed_key != baseline_key
    assert re.fullmatch(r"[0-9a-f]{64}", changed_key)


@pytest.mark.parametrize(
    "changed",
    [
        _request(event="APPROVE"),
        _request(body="Different"),
    ],
)
def test_requested_event_and_body_do_not_change_operation_identity(
    changed: GitHubReviewRequest,
) -> None:
    assert compute_review_operation_key(changed) == compute_review_operation_key(_request())
    assert canonicalize_review_request(changed) != canonicalize_review_request(_request())


def test_reordered_findings_have_the_same_semantic_identity() -> None:
    reordered = _request(comments=tuple(reversed(_request().comments)))
    assert canonicalize_review_request(reordered) == canonicalize_review_request(_request())
    assert compute_review_operation_key(reordered) == compute_review_operation_key(_request())


def test_reordered_findings_differing_only_by_start_side_have_stable_identity() -> None:
    comments = (
        GitHubReviewComment(
            path="src/a.py",
            line=10,
            start_line=8,
            side="RIGHT",
            start_side="LEFT",
            body="Same body",
        ),
        GitHubReviewComment(
            path="src/a.py",
            line=10,
            start_line=8,
            side="RIGHT",
            start_side="RIGHT",
            body="Same body",
        ),
    )
    baseline = _request(comments=comments)
    reordered = _request(comments=tuple(reversed(comments)))

    assert canonicalize_review_request(reordered) == canonicalize_review_request(baseline)
    assert compute_review_operation_key(reordered) == compute_review_operation_key(baseline)


def test_repository_identity_is_validated_and_case_normalized() -> None:
    mixed_case = _request(repository="TalonT-Org/AutoSkillit")
    lowercase = _request(repository="talont-org/autoskillit")

    assert canonicalize_review_request(mixed_case) == canonicalize_review_request(lowercase)
    assert compute_review_operation_key(mixed_case) == compute_review_operation_key(lowercase)
    assert json.loads(canonicalize_review_request(mixed_case))["repository"] == (
        "talont-org/autoskillit"
    )


@pytest.mark.parametrize("repository", ["", "not-a-repository", "owner/repo/extra"])
def test_malformed_repository_identity_is_rejected(repository: str) -> None:
    with pytest.raises(ValueError, match="canonical owner/repo"):
        compute_review_operation_key(_request(repository=repository))


def test_canonicalization_does_not_mutate_the_request() -> None:
    request = _request()
    before = request.comments
    canonicalize_review_request(request)
    compute_review_operation_key(request)
    assert request.comments is before
    assert request.comments == _request().comments
