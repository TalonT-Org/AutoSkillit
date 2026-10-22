"""HTTP response classification at the real GitHub review gateway boundary."""

import httpx
import pytest

from autoskillit.core import ReviewResponseClass
from autoskillit.execution.github_review.gateway import DefaultGitHubReviewGateway

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
