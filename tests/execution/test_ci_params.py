"""Tests for CIRunScope query param composition and workflow scoping.

These tests assert the *correctness* of the HTTP request parameters sent to
GitHub Actions API, not merely that requests are made. They were added as
immunity guards against the bug where workflow_id was silently absent.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch

import pytest

from autoskillit.core import CIRunScope
from autoskillit.execution.ci import DefaultCIWatcher


def _now() -> str:
    return datetime.now(UTC).isoformat()


# ---------------------------------------------------------------------------
# _fetch_completed_runs — query param composition
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_completed_runs_includes_workflow_id(httpx_mock):
    """When scope.workflow='tests.yml', workflow_id must appear in API query params."""
    import httpx

    httpx_mock.add_response(json={"workflow_runs": []})
    watcher = DefaultCIWatcher(token="tok")
    async with httpx.AsyncClient() as client:
        await watcher._fetch_completed_runs(
            client,
            watcher._headers(),
            "owner/repo",
            "main",
            scope=CIRunScope(workflow="tests.yml"),
            lookback_seconds=300,
        )
    req = httpx_mock.get_requests()[0]
    assert "workflow_id=tests.yml" in str(req.url)


@pytest.mark.anyio
async def test_completed_runs_omits_workflow_id_when_none(httpx_mock):
    """When scope.workflow is None, workflow_id must be absent from API params."""
    import httpx

    httpx_mock.add_response(json={"workflow_runs": []})
    watcher = DefaultCIWatcher(token="tok")
    async with httpx.AsyncClient() as client:
        await watcher._fetch_completed_runs(
            client,
            watcher._headers(),
            "owner/repo",
            "main",
            scope=CIRunScope(),
            lookback_seconds=300,
        )
    req = httpx_mock.get_requests()[0]
    assert "workflow_id" not in str(req.url)


@pytest.mark.anyio
async def test_completed_runs_always_sends_branch(httpx_mock):
    """branch must always appear in API params regardless of scope."""
    import httpx

    httpx_mock.add_response(json={"workflow_runs": []})
    watcher = DefaultCIWatcher(token="tok")
    async with httpx.AsyncClient() as client:
        await watcher._fetch_completed_runs(
            client,
            watcher._headers(),
            "owner/repo",
            "main",
            scope=CIRunScope(),
            lookback_seconds=300,
        )
    req = httpx_mock.get_requests()[0]
    assert "branch=main" in str(req.url)


@pytest.mark.anyio
async def test_completed_runs_sends_head_sha(httpx_mock):
    """When scope.head_sha is set, head_sha must appear in API params."""
    import httpx

    httpx_mock.add_response(json={"workflow_runs": []})
    watcher = DefaultCIWatcher(token="tok")
    async with httpx.AsyncClient() as client:
        await watcher._fetch_completed_runs(
            client,
            watcher._headers(),
            "owner/repo",
            "main",
            scope=CIRunScope(head_sha="abc123"),
            lookback_seconds=300,
        )
    req = httpx_mock.get_requests()[0]
    assert "head_sha=abc123" in str(req.url)


# ---------------------------------------------------------------------------
# _fetch_active_runs — query param composition
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_active_runs_includes_workflow_id(httpx_mock):
    """When scope.workflow='tests.yml', workflow_id must appear in active runs params."""
    import httpx

    httpx_mock.add_response(json={"workflow_runs": []})
    watcher = DefaultCIWatcher(token="tok")
    async with httpx.AsyncClient() as client:
        await watcher._fetch_active_runs(
            client,
            watcher._headers(),
            "owner/repo",
            "main",
            scope=CIRunScope(workflow="tests.yml"),
        )
    req = httpx_mock.get_requests()[0]
    assert "workflow_id=tests.yml" in str(req.url)


# ---------------------------------------------------------------------------
# status() — query param composition
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_status_list_path_includes_workflow_id(httpx_mock):
    """status() branch-list path must send workflow_id when scope carries a workflow."""
    httpx_mock.add_response(json={"workflow_runs": []})
    watcher = DefaultCIWatcher(token="tok")
    await watcher.status("main", repo="owner/repo", scope=CIRunScope(workflow="tests.yml"))
    req = httpx_mock.get_requests()[0]
    assert "workflow_id=tests.yml" in str(req.url)


# ---------------------------------------------------------------------------
# wait() — multi-workflow isolation
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_multi_workflow_selects_correct_workflow(httpx_mock):
    """Both deploy.yml (failure) and tests.yml (success) on same branch.

    With scope.workflow='tests.yml', the API returns only the tests.yml run
    (simulated by the single-run httpx_mock response). Result must be success.
    """
    httpx_mock.add_response(
        json={
            "workflow_runs": [
                {
                    "id": 2,
                    "conclusion": "success",
                    "status": "completed",
                    "workflow_id": 42,
                    "name": "Tests",
                    "updated_at": _now(),
                }
            ]
        }
    )
    watcher = DefaultCIWatcher(token="tok")
    result = await watcher.wait(
        "main",
        repo="owner/repo",
        scope=CIRunScope(workflow="tests.yml"),
        timeout_seconds=60,
    )
    assert result["conclusion"] == "success"


# ---------------------------------------------------------------------------
# _fetch_failed_jobs — FAILED_CONCLUSIONS coverage
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_failed_jobs_includes_timed_out(httpx_mock):
    """_fetch_failed_jobs must include jobs with timed_out conclusion."""
    import httpx

    jobs = [
        {"name": "build", "conclusion": "timed_out"},
        {"name": "lint", "conclusion": "failure"},
        {"name": "ok", "conclusion": "success"},
    ]
    httpx_mock.add_response(json={"jobs": jobs})
    watcher = DefaultCIWatcher(token="tok")
    async with httpx.AsyncClient() as client:
        result = await watcher._fetch_failed_jobs(
            client, watcher._headers(), "owner/repo", 1
        )
    assert "build" in result  # timed_out must appear
    assert "lint" in result
    assert "ok" not in result


@pytest.mark.anyio
async def test_wait_calls_fetch_jobs_for_timed_out_run(httpx_mock):
    """A GitHub-level timed_out run conclusion must still populate failed_jobs."""
    httpx_mock.add_response(
        json={
            "workflow_runs": [
                {
                    "id": 5,
                    "conclusion": "timed_out",
                    "status": "completed",
                    "updated_at": _now(),
                }
            ]
        }
    )
    httpx_mock.add_response(json={"jobs": [{"name": "unit", "conclusion": "timed_out"}]})
    watcher = DefaultCIWatcher(token="tok")
    result = await watcher.wait("main", repo="owner/repo", timeout_seconds=60)
    assert result["conclusion"] == "timed_out"
    assert "unit" in result["failed_jobs"]


# ---------------------------------------------------------------------------
# MCP handler — workflow passed via scope
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_wait_for_ci_handler_passes_workflow(tool_ctx):
    """wait_for_ci MCP handler must forward workflow to watcher via scope."""
    from autoskillit.server.tools_ci import wait_for_ci

    mock_watcher = AsyncMock()
    mock_watcher.wait = AsyncMock(
        return_value={"conclusion": "success", "failed_jobs": [], "run_id": 1}
    )
    tool_ctx.ci_watcher = mock_watcher

    # cwd="" → head_sha inference skipped (empty string is falsy)
    json.loads(await wait_for_ci(branch="main", workflow="tests.yml", cwd=""))

    call_kwargs = mock_watcher.wait.call_args
    assert call_kwargs.kwargs["scope"].workflow == "tests.yml"


@pytest.mark.anyio
async def test_get_ci_status_handler_passes_workflow(tool_ctx):
    """get_ci_status MCP handler must forward workflow to watcher via scope."""
    from autoskillit.server.tools_ci import get_ci_status

    mock_watcher = AsyncMock()
    mock_watcher.status = AsyncMock(return_value={"runs": []})
    tool_ctx.ci_watcher = mock_watcher

    await get_ci_status(branch="main", workflow="tests.yml")

    call_kwargs = mock_watcher.status.call_args
    assert call_kwargs.kwargs["scope"].workflow == "tests.yml"
