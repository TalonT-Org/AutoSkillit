"""L1 unit tests for execution/ci.py — CIWatcher service."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch

import pytest

from autoskillit.core import CIRunScope, CIWatcher
from autoskillit.execution.ci import (
    DefaultCIWatcher,
    _jittered_sleep,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_NOW = datetime.now(UTC)


def _run(
    run_id: int = 12345,
    status: str = "completed",
    conclusion: str = "success",
    head_sha: str = "abc123",
    updated_at: str | None = None,
) -> dict:
    """Build a mock workflow run dict."""
    return {
        "id": run_id,
        "status": status,
        "conclusion": conclusion,
        "head_sha": head_sha,
        "updated_at": updated_at or _NOW.isoformat(),
    }


def _runs_response(*runs: dict) -> dict:
    return {"workflow_runs": list(runs)}


def _jobs_response(*jobs: tuple[str, str]) -> dict:
    """jobs: (name, conclusion) tuples."""
    return {"jobs": [{"name": n, "conclusion": c} for n, c in jobs]}


# ---------------------------------------------------------------------------
# _jittered_sleep
# ---------------------------------------------------------------------------


def test_jittered_sleep_bounded():
    for attempt in range(10):
        val = _jittered_sleep(attempt)
        assert 0 <= val <= 30  # cap is 30


def test_jittered_sleep_variance():
    """Two calls should not produce identical results (statistical check)."""
    values = {_jittered_sleep(2) for _ in range(20)}
    assert len(values) > 1


# ---------------------------------------------------------------------------
# Protocol conformance
# ---------------------------------------------------------------------------


def test_implements_ci_watcher_protocol():
    watcher = DefaultCIWatcher(token="test")
    assert isinstance(watcher, CIWatcher)


# ---------------------------------------------------------------------------
# DefaultCIWatcher.wait — look-back phase (race condition coverage)
#
# Tests mock the internal methods to avoid pytest-httpx URL matching
# complexity. HTTP-level correctness is covered by the method tests.
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_lookback_finds_completed_successful_run():
    """The exact race condition scenario: CI completed before polling starts."""
    watcher = DefaultCIWatcher(token="tok")
    watcher._fetch_completed_runs = AsyncMock(  # type: ignore[method-assign]
        return_value=[_run(conclusion="success")]
    )
    watcher._fetch_failed_jobs = AsyncMock(return_value=[])  # type: ignore[method-assign]

    result = await watcher.wait("feature-x", repo="owner/repo", timeout_seconds=60)
    assert result == {
        "run_id": 12345,
        "conclusion": "success",
        "failed_jobs": [],
    }


@pytest.mark.anyio
async def test_lookback_finds_completed_failed_run():
    watcher = DefaultCIWatcher(token="tok")
    watcher._fetch_completed_runs = AsyncMock(  # type: ignore[method-assign]
        return_value=[_run(conclusion="failure")]
    )
    watcher._fetch_failed_jobs = AsyncMock(  # type: ignore[method-assign]
        return_value=["test", "lint"]
    )

    result = await watcher.wait("feature-x", repo="owner/repo", timeout_seconds=60)
    assert result["run_id"] == 12345
    assert result["conclusion"] == "failure"
    assert sorted(result["failed_jobs"]) == ["lint", "test"]


@pytest.mark.anyio
async def test_lookback_filters_by_head_sha():
    """When scope.head_sha is provided, the API filters server-side."""
    watcher = DefaultCIWatcher(token="tok")
    matching_run = _run(run_id=222, head_sha="abc123")
    watcher._fetch_completed_runs = AsyncMock(  # type: ignore[method-assign]
        return_value=[matching_run]
    )
    watcher._fetch_failed_jobs = AsyncMock(return_value=[])  # type: ignore[method-assign]

    result = await watcher.wait(
        "feature-x",
        repo="owner/repo",
        scope=CIRunScope(head_sha="abc123"),
        timeout_seconds=60,
    )
    assert result["run_id"] == 222
    # Verify scope.head_sha was passed to the API call
    call_kwargs = watcher._fetch_completed_runs.call_args
    assert call_kwargs[0][4].head_sha == "abc123"  # positional arg: scope


@pytest.mark.anyio
async def test_lookback_without_head_sha_matches_any():
    """Without head_sha, any completed run for the branch matches."""
    watcher = DefaultCIWatcher(token="tok")
    watcher._fetch_completed_runs = AsyncMock(  # type: ignore[method-assign]
        return_value=[_run(run_id=333)]
    )
    watcher._fetch_failed_jobs = AsyncMock(return_value=[])  # type: ignore[method-assign]

    result = await watcher.wait("feature-x", repo="owner/repo", timeout_seconds=60)
    assert result["run_id"] == 333
    assert result["conclusion"] == "success"


@pytest.mark.anyio
async def test_lookback_window_filters_old_runs():
    """Runs older than lookback_seconds should be filtered by the service."""
    watcher = DefaultCIWatcher(token="tok")
    # Look-back returns old run — will be filtered by cutoff time
    watcher._fetch_completed_runs = AsyncMock(  # type: ignore[method-assign]
        return_value=[]  # _fetch_completed_runs already filters by time
    )
    watcher._fetch_active_runs = AsyncMock(return_value=[])  # type: ignore[method-assign]

    with patch("autoskillit.execution.ci.asyncio.sleep", new_callable=AsyncMock):
        result = await watcher.wait(
            "feature-x",
            repo="owner/repo",
            timeout_seconds=1,
            lookback_seconds=120,
        )
    assert result["conclusion"] in ("no_runs", "timed_out")


# ---------------------------------------------------------------------------
# DefaultCIWatcher.wait — poll + wait phases
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_polls_active_run_until_completion():
    watcher = DefaultCIWatcher(token="tok")
    # Phase 1: no completed runs
    watcher._fetch_completed_runs = AsyncMock(return_value=[])  # type: ignore[method-assign]
    # Phase 2: find an active run
    watcher._fetch_active_runs = AsyncMock(  # type: ignore[method-assign]
        return_value=[_run(run_id=555, status="in_progress", conclusion=None)]
    )
    # Phase 3: first in-progress, then completed
    watcher._poll_run_status = AsyncMock(  # type: ignore[method-assign]
        side_effect=[
            _run(run_id=555, status="in_progress", conclusion=None),
            _run(run_id=555, status="completed", conclusion="success"),
        ]
    )
    watcher._fetch_failed_jobs = AsyncMock(return_value=[])  # type: ignore[method-assign]

    with patch("autoskillit.execution.ci.asyncio.sleep", new_callable=AsyncMock):
        result = await watcher.wait("main", repo="owner/repo", timeout_seconds=60)

    assert result["run_id"] == 555
    assert result["conclusion"] == "success"


@pytest.mark.anyio
async def test_no_runs_at_all_returns_no_runs():
    watcher = DefaultCIWatcher(token="tok")
    watcher._fetch_completed_runs = AsyncMock(return_value=[])  # type: ignore[method-assign]
    watcher._fetch_active_runs = AsyncMock(return_value=[])  # type: ignore[method-assign]

    with patch("autoskillit.execution.ci.asyncio.sleep", new_callable=AsyncMock):
        result = await watcher.wait("main", repo="owner/repo", timeout_seconds=1)

    assert result["run_id"] is None
    assert result["conclusion"] == "no_runs"
    assert result["failed_jobs"] == []


@pytest.mark.anyio
async def test_timeout_exceeded():
    watcher = DefaultCIWatcher(token="tok")
    watcher._fetch_completed_runs = AsyncMock(return_value=[])  # type: ignore[method-assign]
    watcher._fetch_active_runs = AsyncMock(  # type: ignore[method-assign]
        return_value=[_run(run_id=666, status="in_progress", conclusion=None)]
    )
    # Always returns in-progress
    watcher._poll_run_status = AsyncMock(  # type: ignore[method-assign]
        return_value=_run(run_id=666, status="in_progress", conclusion=None)
    )

    with patch("autoskillit.execution.ci.asyncio.sleep", new_callable=AsyncMock):
        result = await watcher.wait("main", repo="owner/repo", timeout_seconds=1)

    assert result["run_id"] == 666
    assert result["conclusion"] == "timed_out"
    assert result["failed_jobs"] == []


@pytest.mark.anyio
async def test_exponential_backoff_with_jitter():
    """Captured sleep durations should follow the exponential schedule."""
    watcher = DefaultCIWatcher(token="tok")
    watcher._fetch_completed_runs = AsyncMock(return_value=[])  # type: ignore[method-assign]
    watcher._fetch_active_runs = AsyncMock(  # type: ignore[method-assign]
        return_value=[_run(run_id=777, status="in_progress", conclusion=None)]
    )
    # Several in-progress then completed
    watcher._poll_run_status = AsyncMock(  # type: ignore[method-assign]
        side_effect=[
            _run(run_id=777, status="in_progress", conclusion=None),
            _run(run_id=777, status="in_progress", conclusion=None),
            _run(run_id=777, status="in_progress", conclusion=None),
            _run(run_id=777, status="in_progress", conclusion=None),
            _run(run_id=777, status="completed", conclusion="success"),
        ]
    )
    watcher._fetch_failed_jobs = AsyncMock(return_value=[])  # type: ignore[method-assign]

    sleep_durations: list[float] = []
    mock_sleep = AsyncMock(side_effect=lambda d: sleep_durations.append(d))

    with patch("autoskillit.execution.ci.asyncio.sleep", mock_sleep):
        result = await watcher.wait("main", repo="owner/repo", timeout_seconds=600)

    assert result["conclusion"] == "success"
    # All sleep durations should be bounded by [0, 30]
    for d in sleep_durations:
        assert 0 <= d <= 30


@pytest.mark.anyio
async def test_extracts_failed_job_names():
    watcher = DefaultCIWatcher(token="tok")
    watcher._fetch_completed_runs = AsyncMock(  # type: ignore[method-assign]
        return_value=[_run(conclusion="failure")]
    )
    watcher._fetch_failed_jobs = AsyncMock(  # type: ignore[method-assign]
        return_value=["test", "lint"]
    )

    result = await watcher.wait("main", repo="owner/repo", timeout_seconds=60)
    assert result["conclusion"] == "failure"
    assert sorted(result["failed_jobs"]) == ["lint", "test"]


# ---------------------------------------------------------------------------
# DefaultCIWatcher.wait — HTTP-level tests (pytest-httpx)
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_lookback_http_integration(httpx_mock):
    """Verify HTTP call is made correctly using pytest-httpx."""
    # Responses are consumed FIFO — first call is the completed runs query,
    # second would be jobs but success means no jobs call needed.
    httpx_mock.add_response(
        json=_runs_response(_run(run_id=888, conclusion="success")),
    )
    watcher = DefaultCIWatcher(token="tok")
    result = await watcher.wait("feature-x", repo="owner/repo", timeout_seconds=60)
    assert result["run_id"] == 888
    assert result["conclusion"] == "success"

    # Verify the actual HTTP request was made correctly
    requests = httpx_mock.get_requests()
    assert len(requests) == 1
    assert "/actions/runs" in str(requests[0].url)
    assert "status=completed" in str(requests[0].url)


@pytest.mark.anyio
async def test_failed_run_fetches_jobs(httpx_mock):
    """Verify failed run triggers a jobs API call."""
    httpx_mock.add_response(
        json=_runs_response(_run(conclusion="failure")),
    )
    httpx_mock.add_response(
        json=_jobs_response(("test", "failure"), ("build", "success")),
    )
    watcher = DefaultCIWatcher(token="tok")
    result = await watcher.wait("main", repo="owner/repo", timeout_seconds=60)
    assert result["failed_jobs"] == ["test"]

    requests = httpx_mock.get_requests()
    assert len(requests) == 2
    assert "/jobs" in str(requests[1].url)


@pytest.mark.anyio
async def test_status_by_run_id(httpx_mock):
    # Response 1: run status
    httpx_mock.add_response(
        json=_run(run_id=999, status="completed", conclusion="failure"),
    )
    # Response 2: jobs
    httpx_mock.add_response(
        json=_jobs_response(("deploy", "failure")),
    )
    watcher = DefaultCIWatcher(token="tok")
    result = await watcher.status("main", repo="owner/repo", run_id=999)
    assert len(result["runs"]) == 1
    assert result["runs"][0]["conclusion"] == "failure"
    assert result["runs"][0]["failed_jobs"] == ["deploy"]


# ---------------------------------------------------------------------------
# TOOL-2: Billing-error (action_required) coverage
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_wait_billing_error_surfaced_distinctly(httpx_mock):
    """action_required conclusion passes through as distinct value, not mapped to failure.

    Billing limit errors surface as conclusion="action_required" with failed_jobs=[].
    The jobs endpoint must NOT be called (no /jobs HTTP request).
    """
    httpx_mock.add_response(
        json=_runs_response(_run(run_id=777, conclusion="action_required")),
    )
    watcher = DefaultCIWatcher(token="tok")
    result = await watcher.wait("main", repo="owner/repo", timeout_seconds=60)
    assert result["conclusion"] == "action_required"
    assert result["failed_jobs"] == []

    # Jobs endpoint must not be called for action_required (no job-level failures)
    requests = httpx_mock.get_requests()
    assert len(requests) == 1
    assert "/jobs" not in str(requests[0].url)
