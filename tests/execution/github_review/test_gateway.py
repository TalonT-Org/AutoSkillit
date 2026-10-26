"""HTTP response classification at the real GitHub review gateway boundary."""

import httpx
import pytest

from autoskillit.core import ReviewResponseClass
from autoskillit.execution.github_review.gateway import DefaultGitHubReviewGateway
from tests.fixtures.github_rest import (
    pulls_comments_outdated_right,
    pulls_comments_relocated_right,
)

pytestmark = [pytest.mark.layer("execution"), pytest.mark.small]


@pytest.mark.anyio
@pytest.mark.parametrize(
    "status,message,expected",
    [
        (201, "Created", ReviewResponseClass.SUCCESS),
        (422, "Validation Failed", ReviewResponseClass.VALIDATION_ERROR),
        (502, "Bad Gateway", ReviewResponseClass.SERVER_ERROR),
        (401, "Bad credentials", ReviewResponseClass.CLIENT_ERROR),
        (403, "Resource not accessible by integration", ReviewResponseClass.CLIENT_ERROR),
        (
            403,
            "You have exceeded a secondary rate limit",
            ReviewResponseClass.SECONDARY_RATE_LIMIT,
        ),
        (
            403,
            "You have triggered an abuse detection mechanism",
            ReviewResponseClass.SECONDARY_RATE_LIMIT,
        ),
        (429, "Too Many Requests", ReviewResponseClass.SECONDARY_RATE_LIMIT),
    ],
)
async def test_gateway_classifies_http_responses(
    status: int, message: str, expected: ReviewResponseClass
) -> None:
    transport = httpx.MockTransport(
        lambda request: httpx.Response(status, json={"message": message})
    )
    gateway = DefaultGitHubReviewGateway(
        token_factory=lambda: "test-token",
        api_log=None,
        client_factory=lambda **kwargs: httpx.AsyncClient(transport=transport, **kwargs),
    )

    result = await gateway.create_review("owner/repo", 1, {"event": "COMMENT", "body": "Review"})

    assert result.status_code == status
    assert result.response_class is expected


@pytest.mark.anyio
async def test_review_comment_collection_uses_pr_wide_endpoint_and_paginates() -> None:
    target = pulls_comments_relocated_right()
    review_id = target["pull_request_review_id"]
    assert isinstance(review_id, int)
    foreign_review = {
        **pulls_comments_outdated_right(),
        "pull_request_review_id": 88,
    }
    legacy_shape = {
        "id": target["id"],
        "pull_request_review_id": target["pull_request_review_id"],
        "path": target["path"],
        "position": 37,
        "original_position": 37,
    }
    trace: list[tuple[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        trace.append((request.method, str(request.url)))
        if request.url.path.endswith(f"/reviews/{review_id}/comments"):
            return httpx.Response(200, json=[legacy_shape])
        if (
            request.url.path.endswith("/pulls/1/comments")
            and request.url.params.get("page") == "2"
        ):
            return httpx.Response(200, json=[target], headers={"x-page": "2"})
        if request.url.path.endswith("/pulls/1/comments"):
            return httpx.Response(
                200,
                json=[foreign_review],
                headers={
                    "link": (
                        "<https://api.github.com/repos/owner/repo/pulls/1/comments?"
                        'per_page=100&page=2>; rel="next"'
                    ),
                    "x-page": "1",
                },
            )
        raise AssertionError(f"unexpected request: {request.method} {request.url}")

    transport = httpx.MockTransport(handler)
    gateway = DefaultGitHubReviewGateway(
        token_factory=lambda: "test-token",
        api_log=None,
        client_factory=lambda **kwargs: httpx.AsyncClient(transport=transport, **kwargs),
    )

    result = await gateway.list_pull_review_comments("owner/repo", 1)

    assert result.succeeded
    assert result.data == [foreign_review, target]
    assert trace == [
        ("GET", "https://api.github.com/repos/owner/repo/pulls/1/comments?per_page=100"),
        (
            "GET",
            "https://api.github.com/repos/owner/repo/pulls/1/comments?per_page=100&page=2",
        ),
    ]


@pytest.mark.anyio
async def test_review_comment_collection_returns_later_page_http_failure() -> None:
    trace: list[tuple[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        trace.append((request.method, str(request.url)))
        if (
            request.url.path.endswith("/pulls/1/comments")
            and request.url.params.get("page") == "2"
        ):
            return httpx.Response(
                502,
                json={"message": "Bad Gateway"},
                headers={"x-page": "2"},
            )
        if request.url.path.endswith("/pulls/1/comments"):
            return httpx.Response(
                200,
                json=[pulls_comments_relocated_right()],
                headers={
                    "link": (
                        "<https://api.github.com/repos/owner/repo/pulls/1/comments?"
                        'per_page=100&page=2>; rel="next"'
                    )
                },
            )
        raise AssertionError(f"unexpected request: {request.method} {request.url}")

    transport = httpx.MockTransport(handler)
    gateway = DefaultGitHubReviewGateway(
        token_factory=lambda: "test-token",
        api_log=None,
        client_factory=lambda **kwargs: httpx.AsyncClient(transport=transport, **kwargs),
    )

    result = await gateway.list_pull_review_comments("owner/repo", 1)

    assert result.status_code == 502
    assert result.response_class is ReviewResponseClass.SERVER_ERROR
    assert result.data == {"message": "Bad Gateway"}
    assert result.headers["x-page"] == "2"
    assert trace == [
        ("GET", "https://api.github.com/repos/owner/repo/pulls/1/comments?per_page=100"),
        (
            "GET",
            "https://api.github.com/repos/owner/repo/pulls/1/comments?per_page=100&page=2",
        ),
    ]


@pytest.mark.anyio
@pytest.mark.parametrize(
    "link",
    [
        'https://api.github.com/repos/owner/repo/pulls/1/comments?page=2; rel="next"',
        '<https://example.test/repos/owner/repo/pulls/1/comments?page=2>; rel="next"',
    ],
)
async def test_review_comment_collection_rejects_unsafe_next_link(link: str) -> None:
    trace: list[tuple[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        trace.append((request.method, str(request.url)))
        if request.url.path.endswith("/pulls/1/comments"):
            return httpx.Response(
                200,
                json=[pulls_comments_relocated_right()],
                headers={"link": link, "x-page": "1"},
            )
        raise AssertionError(f"unexpected request: {request.method} {request.url}")

    transport = httpx.MockTransport(handler)
    gateway = DefaultGitHubReviewGateway(
        token_factory=lambda: "test-token",
        api_log=None,
        client_factory=lambda **kwargs: httpx.AsyncClient(transport=transport, **kwargs),
    )

    result = await gateway.list_pull_review_comments("owner/repo", 1)

    assert result.status_code == 200
    assert result.response_class is ReviewResponseClass.CLIENT_ERROR
    assert result.data is None
    assert result.headers["x-page"] == "1"
    assert result.error
    assert trace == [
        ("GET", "https://api.github.com/repos/owner/repo/pulls/1/comments?per_page=100")
    ]
