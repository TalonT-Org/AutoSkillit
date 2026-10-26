"""GitHub REST response bodies captured from actual API calls."""

from typing import Any


def reviews_create_422_author_approve() -> dict[str, object]:
    """POST /repos/{owner}/{repo}/pulls/{pull_number}/reviews, status 422.

    Captured 2026-06-27 UTC, PR #4128, reviewer == author, APPROVE.
    ``gh api … --method POST`` output; body is the GitHub response JSON.
    """
    return {
        "message": "Unprocessable Entity",
        "errors": ["Review Can not approve your own pull request"],
        "documentation_url": (
            "https://docs.github.com/rest/pulls/reviews#create-a-review-for-a-pull-request"
        ),
        "status": "422",
    }


def reviews_create_422_author_request_changes() -> dict[str, object]:
    """POST /repos/{owner}/{repo}/pulls/{pull_number}/reviews, status 422.

    Captured 2026-07-17 UTC, PR #4277, reviewer == author, REQUEST_CHANGES.
    ``gh api … --method POST`` output; body is the GitHub response JSON.
    """
    return {
        "message": "Unprocessable Entity",
        "errors": ["Review Can not request changes on your own pull request"],
        "documentation_url": (
            "https://docs.github.com/rest/pulls/reviews#create-a-review-for-a-pull-request"
        ),
        "status": "422",
    }


def reviews_create_422_line_unresolved() -> dict[str, object]:
    """POST /repos/{owner}/{repo}/pulls/{pull_number}/reviews, status 422.

    Captured 2026-07-31 UTC, PR #4427, inline comment outside the resolvable diff.
    ``gh api … --method POST`` output; body is the GitHub response JSON.
    """
    return {
        "message": "Unprocessable Entity",
        "errors": ["Line could not be resolved"],
        "documentation_url": (
            "https://docs.github.com/rest/pulls/reviews#create-a-review-for-a-pull-request"
        ),
        "status": "422",
    }


def pulls_comments_relocated_right() -> dict[str, Any]:
    """PR #4427 comment 3687217371, read via PR-wide REST on 2026-10-26.

    Coordinate fields are observed; tests supply synthetic marker bodies.
    """
    return {
        "id": 3687217371,
        "pull_request_review_id": 4824396371,
        "path": "src/autoskillit/core/types/_type_audit_admission.py",
        "commit_id": "a4ff444c57a71246d0f2ae63162db402cf6c26f4",
        "original_commit_id": "16fd3411cbc71640e14976d95fe67d7ff091f317",
        "line": 119,
        "original_line": 124,
        "start_line": None,
        "original_start_line": None,
        "side": "RIGHT",
        "start_side": None,
    }


def pulls_comments_outdated_right() -> dict[str, Any]:
    """PR #4427 comment 3687217360, observed null-current anchor on 2026-10-26."""
    return {
        "id": 3687217360,
        "pull_request_review_id": 4824396371,
        "path": "docs/execution/architecture.md",
        "commit_id": "16fd3411cbc71640e14976d95fe67d7ff091f317",
        "original_commit_id": "16fd3411cbc71640e14976d95fe67d7ff091f317",
        "line": None,
        "original_line": 28,
        "start_line": None,
        "original_start_line": None,
        "side": "RIGHT",
        "start_side": None,
    }


def pulls_comments_relocated_left() -> dict[str, Any]:
    """Sandbox PR #50 comment 4380339489, captured 2026-10-26.

    Submitted LEFT 11–13 at e9b2cca1; shifting both disposable branches by
    five lines moved the current range to 16–18. Coordinates are unmodified;
    tests supply synthetic finding bodies and review-author identities.
    """
    return {
        "id": 4380339489,
        "pull_request_review_id": 5656216171,
        "path": "left_multiline_fixture.txt",
        "commit_id": "a7e6d89767a9659d0d8854306defbe5e675c8b55",
        "original_commit_id": "e9b2cca1c9b04af14ab43b376cbcfe004ff3dd40",
        "line": 18,
        "original_line": 13,
        "start_line": 16,
        "original_start_line": 11,
        "side": "LEFT",
        "start_side": "LEFT",
    }
