"""GitHub REST response bodies captured from actual API calls."""


def reviews_create_422_author_approve() -> dict[str, object]:
    """POST /repos/{owner}/{repo}/pulls/{pull_number}/reviews, status 422.

    Captured 2026-06-27 UTC, PR #4128, reviewer == author, APPROVE.
    Claude Code session cb891092-6d3b-4b83-a84e-d355419f7546,
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
    Claude Code session a6fb9ee7-5c9f-404e-9e88-3abd850a23a8,
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
    Claude Code session c671af21-a24b-451c-ae39-75dab101b63d,
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
