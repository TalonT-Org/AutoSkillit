import asyncio

import pytest

from autoskillit.pipeline.github_api_log import DefaultGitHubApiLog

pytestmark = [pytest.mark.layer("pipeline"), pytest.mark.small, pytest.mark.anyio]


async def test_empty_log_returns_none_usage():
    log = DefaultGitHubApiLog()
    assert log.to_usage("sess-1") is None


async def test_record_httpx_increments_total():
    log = DefaultGitHubApiLog()
    await log.record_httpx(
        method="GET",
        path="/repos/owner/repo/issues/1",
        status_code=200,
        latency_ms=120.0,
        rate_limit_remaining=4900,
        rate_limit_used=100,
        rate_limit_reset=1714000000,
        timestamp="2026-04-27T10:00:00Z",
    )
    usage = log.to_usage("sess-1")
    assert usage is not None
    assert usage["total_requests"] == 1
    assert usage["by_source"]["httpx"] == 1


async def test_record_gh_cli_increments_total():
    log = DefaultGitHubApiLog()
    await log.record_gh_cli(
        subcommand="gh pr list",
        exit_code=0,
        latency_ms=85.0,
        timestamp="2026-04-27T10:00:01Z",
    )
    usage = log.to_usage("sess-1")
    assert usage is not None
    assert usage["total_requests"] == 1
    assert usage["by_source"]["gh_cli"] == 1


async def test_endpoint_categorization():
    log = DefaultGitHubApiLog()
    paths = [
        ("/repos/o/r/issues/1", "issues"),
        ("/repos/o/r/pulls/2", "pulls"),
        ("/repos/o/r/actions/runs", "actions"),
        ("/search/issues", "search"),
        ("/graphql", "graphql"),
        ("/repos/o/r/labels", "other"),
    ]
    for path, expected_category in paths:
        await log.record_httpx(
            method="GET",
            path=path,
            status_code=200,
            latency_ms=10.0,
            rate_limit_remaining=4999,
            rate_limit_used=1,
            rate_limit_reset=0,
            timestamp="2026-04-27T10:00:00Z",
        )
    usage = log.to_usage("sess-1")
    assert usage["by_category"]["issues"] == 1
    assert usage["by_category"]["pulls"] == 1
    assert usage["by_category"]["actions"] == 1
    assert usage["by_category"]["search"] == 1
    assert usage["by_category"]["graphql"] == 1
    assert usage["by_category"]["other"] == 1


async def test_min_rate_limit_remaining_tracks_minimum():
    log = DefaultGitHubApiLog()
    for remaining in [4900, 4500, 4800]:
        await log.record_httpx(
            method="GET",
            path="/repos/o/r/issues/1",
            status_code=200,
            latency_ms=10.0,
            rate_limit_remaining=remaining,
            rate_limit_used=0,
            rate_limit_reset=0,
            timestamp="2026-04-27T10:00:00Z",
        )
    usage = log.to_usage("sess-1")
    assert usage["min_rate_limit_remaining"] == 4500


async def test_error_categorization():
    log = DefaultGitHubApiLog()
    await log.record_httpx(
        method="GET",
        path="/repos/o/r/issues/1",
        status_code=404,
        latency_ms=50.0,
        rate_limit_remaining=4999,
        rate_limit_used=1,
        rate_limit_reset=0,
        timestamp="2026-04-27T10:00:00Z",
    )
    await log.record_httpx(
        method="POST",
        path="/repos/o/r/issues",
        status_code=500,
        latency_ms=200.0,
        rate_limit_remaining=4998,
        rate_limit_used=2,
        rate_limit_reset=0,
        timestamp="2026-04-27T10:00:01Z",
    )
    usage = log.to_usage("sess-1")
    assert usage["errors"]["4xx"] == 1
    assert usage["errors"]["5xx"] == 1


async def test_latency_aggregation():
    log = DefaultGitHubApiLog()
    await log.record_httpx(
        method="GET",
        path="/repos/o/r/issues/1",
        status_code=200,
        latency_ms=100.0,
        rate_limit_remaining=4999,
        rate_limit_used=1,
        rate_limit_reset=0,
        timestamp="2026-04-27T10:00:00Z",
    )
    await log.record_httpx(
        method="GET",
        path="/repos/o/r/issues/2",
        status_code=200,
        latency_ms=200.0,
        rate_limit_remaining=4998,
        rate_limit_used=2,
        rate_limit_reset=0,
        timestamp="2026-04-27T10:00:01Z",
    )
    usage = log.to_usage("sess-1")
    assert usage["total_latency_ms"] == pytest.approx(300.0)
    assert usage["avg_latency_ms"] == pytest.approx(150.0)


async def test_first_last_timestamps():
    log = DefaultGitHubApiLog()
    await log.record_httpx(
        method="GET",
        path="/repos/o/r/issues/1",
        status_code=200,
        latency_ms=10.0,
        rate_limit_remaining=4999,
        rate_limit_used=1,
        rate_limit_reset=0,
        timestamp="2026-04-27T10:00:00Z",
    )
    await log.record_httpx(
        method="GET",
        path="/repos/o/r/issues/2",
        status_code=200,
        latency_ms=10.0,
        rate_limit_remaining=4998,
        rate_limit_used=2,
        rate_limit_reset=0,
        timestamp="2026-04-27T10:04:32Z",
    )
    usage = log.to_usage("sess-1")
    assert usage["first_request_ts"] == "2026-04-27T10:00:00Z"
    assert usage["last_request_ts"] == "2026-04-27T10:04:32Z"


async def test_clear_resets_state():
    log = DefaultGitHubApiLog()
    await log.record_httpx(
        method="GET",
        path="/repos/o/r/issues/1",
        status_code=200,
        latency_ms=10.0,
        rate_limit_remaining=4999,
        rate_limit_used=1,
        rate_limit_reset=0,
        timestamp="2026-04-27T10:00:00Z",
    )
    log.clear()
    assert log.to_usage("sess-1") is None


async def test_concurrent_record_is_safe():
    log = DefaultGitHubApiLog()

    async def _record(i: int) -> None:
        await log.record_httpx(
            method="GET",
            path=f"/repos/o/r/issues/{i}",
            status_code=200,
            latency_ms=10.0,
            rate_limit_remaining=4999,
            rate_limit_used=1,
            rate_limit_reset=0,
            timestamp="2026-04-27T10:00:00Z",
        )

    await asyncio.gather(*[_record(i) for i in range(50)])
    usage = log.to_usage("sess-1")
    assert usage["total_requests"] == 50


async def test_session_id_in_usage():
    log = DefaultGitHubApiLog()
    await log.record_httpx(
        method="GET",
        path="/repos/o/r/issues/1",
        status_code=200,
        latency_ms=10.0,
        rate_limit_remaining=4999,
        rate_limit_used=1,
        rate_limit_reset=0,
        timestamp="2026-04-27T10:00:00Z",
    )
    usage = log.to_usage("my-session-123")
    assert usage["session_id"] == "my-session-123"


async def test_drain_returns_usage_and_clears():
    log = DefaultGitHubApiLog()
    await log.record_httpx(
        method="GET",
        path="/repos/o/r/issues/1",
        status_code=200,
        latency_ms=10.0,
        rate_limit_remaining=4999,
        rate_limit_used=1,
        rate_limit_reset=0,
        timestamp="2026-04-27T10:00:00Z",
    )

    usage = log.drain("sess-drain")

    assert usage is not None
    assert usage["total_requests"] == 1
    assert usage["session_id"] == "sess-drain"

    # After drain the accumulator is empty — to_usage and drain both return None
    assert log.to_usage("sess-drain") is None
    assert log.drain("sess-drain") is None
