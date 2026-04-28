import sys

import pytest

pytestmark = [
    pytest.mark.skipif(sys.platform != "linux", reason="api-simulator Linux-only"),
    pytest.mark.layer("execution"),
    pytest.mark.anyio,
    pytest.mark.medium,
]

_api_sim_http = pytest.importorskip("api_simulator.http")
PyResponseSpec = _api_sim_http.MockResponseSpec

from autoskillit.execution.github import DefaultGitHubFetcher, make_tracked_httpx_client
from tests.fakes import InMemoryGitHubApiLog


@pytest.fixture
def _reset_mock(mock_http_server):
    mock_http_server.reset()
    yield


async def test_tracking_transport_records_request(mock_http_server, _reset_mock):
    mock_http_server.register(
        "GET",
        "/repos/owner/repo/issues/1",
        PyResponseSpec(
            body={"number": 1, "title": "T", "body": "", "labels": [], "state": "open"},
            headers=[
                ("X-RateLimit-Remaining", "4900"),
                ("X-RateLimit-Used", "100"),
                ("X-RateLimit-Reset", "1714000000"),
            ],
        ),
    )
    log = InMemoryGitHubApiLog()
    fetcher = DefaultGitHubFetcher(token="test-token", tracker=log, base_url=mock_http_server.url)
    await fetcher.fetch_issue("owner", "repo", 1)
    assert len(log.httpx_calls) == 1
    call = log.httpx_calls[0]
    assert call["path"] == "/repos/owner/repo/issues/1"
    assert call["status_code"] == 200


async def test_tracking_transport_captures_rate_limit_headers(mock_http_server, _reset_mock):
    mock_http_server.register(
        "GET",
        "/repos/owner/repo/issues/1",
        PyResponseSpec(
            body={"number": 1, "title": "T", "body": "", "labels": [], "state": "open"},
            headers=[
                ("X-RateLimit-Remaining", "42"),
                ("X-RateLimit-Used", "4958"),
                ("X-RateLimit-Reset", "1714000000"),
            ],
        ),
    )
    log = InMemoryGitHubApiLog()
    fetcher = DefaultGitHubFetcher(token="test-token", tracker=log, base_url=mock_http_server.url)
    await fetcher.fetch_issue("owner", "repo", 1)
    assert len(log.httpx_calls) == 1
    assert log.httpx_calls[0]["rate_limit_remaining"] == 42


async def test_tracking_transport_records_4xx_error(mock_http_server, _reset_mock):
    mock_http_server.register(
        "GET",
        "/repos/owner/repo/issues/999",
        PyResponseSpec(status=404, body={"message": "Not Found"}),
    )
    log = InMemoryGitHubApiLog()
    fetcher = DefaultGitHubFetcher(token="test-token", tracker=log, base_url=mock_http_server.url)
    # fetch_issue never raises — 404 is returned as {"success": False, ...}
    await fetcher.fetch_issue("owner", "repo", 999)
    assert len(log.httpx_calls) == 1
    assert log.httpx_calls[0]["status_code"] == 404


async def test_tracking_transport_records_latency(mock_http_server, _reset_mock):
    mock_http_server.register(
        "GET",
        "/repos/owner/repo/issues/1",
        PyResponseSpec(
            body={"number": 1, "title": "T", "body": "", "labels": [], "state": "open"},
            delay_ms=50,
        ),
    )
    log = InMemoryGitHubApiLog()
    fetcher = DefaultGitHubFetcher(token="test-token", tracker=log, base_url=mock_http_server.url)
    await fetcher.fetch_issue("owner", "repo", 1)
    assert len(log.httpx_calls) == 1
    assert log.httpx_calls[0]["latency_ms"] >= 50.0


async def test_no_tracker_still_works(mock_http_server, _reset_mock):
    mock_http_server.register(
        "GET",
        "/repos/owner/repo/issues/1",
        PyResponseSpec(
            body={"number": 1, "title": "T", "body": "", "labels": [], "state": "open"}
        ),
    )
    fetcher = DefaultGitHubFetcher(token="test-token", tracker=None, base_url=mock_http_server.url)
    result = await fetcher.fetch_issue("owner", "repo", 1)
    assert result is not None


async def test_make_tracked_httpx_client_without_tracker_is_normal_client():
    import httpx

    client = make_tracked_httpx_client(None, timeout=httpx.Timeout(10.0))
    assert isinstance(client, httpx.AsyncClient)
    await client.aclose()
