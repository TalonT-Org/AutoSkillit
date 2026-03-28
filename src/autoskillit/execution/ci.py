"""GitHub Actions CI watcher service.

L1 module: depends only on stdlib, httpx, and core/logging.
Never raises — all errors are captured and returned as structured dicts.

Three-phase algorithm eliminates the race condition where CI completes
before polling starts:
  1. Look-back: check for recently-completed runs (catches already-done CI)
  2. Poll: wait for an active run to appear (exponential backoff with jitter)
  3. Wait: poll until the found run completes
"""

from __future__ import annotations

import asyncio
import random
import time
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx

from autoskillit.core import CIRunScope, get_logger
from autoskillit.execution.github import _github_headers

_log = get_logger(__name__)

# Backoff schedule constants
_BACKOFF_BASE = 5  # seconds
_BACKOFF_CAP = 30  # seconds

# GitHub run-level conclusions that indicate a job-level failure worth inspecting.
# "action_required" is intentionally excluded — it signals a billing/permissions
# gate, not a job execution failure, so failed_jobs is always [] for it.
FAILED_CONCLUSIONS: frozenset[str] = frozenset(
    {
        "failure",
        "timed_out",
        "startup_failure",
        "cancelled",
    }
)


def _jittered_sleep(attempt: int) -> float:
    """Compute full-jitter exponential backoff: random(0, min(cap, base * 2^attempt))."""
    ceiling = min(_BACKOFF_CAP, _BACKOFF_BASE * (2**attempt))
    return random.uniform(0, ceiling)


class DefaultCIWatcher:
    """Concrete CI watcher using GitHub REST API via httpx.

    Implements the CIWatcher protocol.
    Never raises — errors are returned as structured dicts.
    """

    def __init__(self, *, token: str | None = None) -> None:
        self._token = token

    def _headers(self) -> dict[str, str]:
        return _github_headers(self._token)

    async def _resolve_repo(self, repo: str | None, cwd: str) -> str | None:
        """Resolve owner/repo from argument or git remote."""
        if not cwd and not repo:
            return None
        from autoskillit.execution.remote_resolver import resolve_remote_repo

        return await resolve_remote_repo(cwd, hint=repo)

    async def _fetch_completed_runs(
        self,
        client: httpx.AsyncClient,
        headers: dict[str, str],
        owner_repo: str,
        branch: str,
        scope: CIRunScope,
        lookback_seconds: int,
    ) -> list[dict[str, Any]]:
        """Phase 1: Look-back — fetch recently completed runs for the branch."""
        url = f"https://api.github.com/repos/{owner_repo}/actions/runs"
        params: dict[str, str | int] = {
            "branch": branch,
            "per_page": 5,
            "status": "completed",
        }
        if scope.workflow:
            params["workflow_id"] = scope.workflow
        if scope.head_sha:
            params["head_sha"] = scope.head_sha

        resp = await client.get(url, headers=headers, params=params)
        resp.raise_for_status()
        data = resp.json()

        cutoff = datetime.now(UTC) - timedelta(seconds=lookback_seconds)
        runs = []
        for run in data.get("workflow_runs", []):
            updated = run.get("updated_at", "")
            if updated:
                try:
                    run_time = datetime.fromisoformat(updated.replace("Z", "+00:00"))
                    if run_time >= cutoff:
                        runs.append(run)
                except ValueError:
                    continue
        return runs

    async def _fetch_active_runs(
        self,
        client: httpx.AsyncClient,
        headers: dict[str, str],
        owner_repo: str,
        branch: str,
        scope: CIRunScope,
    ) -> list[dict[str, Any]]:
        """Fetch active (non-completed) runs for the branch."""
        url = f"https://api.github.com/repos/{owner_repo}/actions/runs"
        params: dict[str, str | int] = {
            "branch": branch,
            "per_page": 1,
        }
        if scope.workflow:
            params["workflow_id"] = scope.workflow
        if scope.head_sha:
            params["head_sha"] = scope.head_sha

        resp = await client.get(url, headers=headers, params=params)
        resp.raise_for_status()
        data = resp.json()

        return [r for r in data.get("workflow_runs", []) if r.get("status") != "completed"]

    async def _poll_run_status(
        self,
        client: httpx.AsyncClient,
        headers: dict[str, str],
        owner_repo: str,
        run_id: int,
    ) -> dict[str, Any]:
        """Fetch a single run's current status."""
        url = f"https://api.github.com/repos/{owner_repo}/actions/runs/{run_id}"
        resp = await client.get(url, headers=headers)
        resp.raise_for_status()
        return resp.json()

    async def _fetch_failed_jobs(
        self,
        client: httpx.AsyncClient,
        headers: dict[str, str],
        owner_repo: str,
        run_id: int,
    ) -> list[str]:
        """Extract failed job names from a completed run.

        Includes all failure-class conclusions: failure, timed_out,
        startup_failure, cancelled.
        """
        url = f"https://api.github.com/repos/{owner_repo}/actions/runs/{run_id}/jobs"
        resp = await client.get(url, headers=headers)
        resp.raise_for_status()
        data = resp.json()
        return [
            j["name"] for j in data.get("jobs", []) if j.get("conclusion") in FAILED_CONCLUSIONS
        ]

    async def wait(
        self,
        branch: str,
        *,
        repo: str | None = None,
        scope: CIRunScope = CIRunScope(),
        timeout_seconds: int = 300,
        lookback_seconds: int = 120,
        cwd: str = "",
    ) -> dict[str, Any]:
        """Wait for a CI run to complete on the given branch.

        Three-phase algorithm:
          1. Look-back: check for recently-completed runs
          2. Poll: wait for an active run to appear
          3. Wait: poll until the found run completes

        Returns: {"run_id": int|None, "conclusion": str, "failed_jobs": list[str]}
        Conclusion values: "success", "failure", "cancelled", "action_required",
        "timed_out", "no_runs", "error", "unknown". Billing limit errors surface
        as conclusion="action_required" with failed_jobs=[].
        Never raises.
        """
        owner_repo = await self._resolve_repo(repo, cwd)
        if not owner_repo:
            return {
                "run_id": None,
                "conclusion": "no_runs",
                "failed_jobs": [],
                "error": "Could not determine repository. Provide repo or cwd.",
            }

        headers = self._headers()
        deadline = time.monotonic() + timeout_seconds

        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(15.0, connect=5.0)) as client:
                # Phase 1: Look-back — check for recently-completed runs
                _log.info(
                    "ci_watcher_lookback",
                    branch=branch,
                    repo=owner_repo,
                    head_sha=scope.head_sha or "(any)",
                    workflow=scope.workflow or "(any)",
                )
                completed = await self._fetch_completed_runs(
                    client,
                    headers,
                    owner_repo,
                    branch,
                    scope,
                    lookback_seconds,
                )
                if completed:
                    run = completed[0]
                    run_id = run["id"]
                    conclusion = run.get("conclusion", "unknown")
                    failed_jobs = (
                        await self._fetch_failed_jobs(
                            client,
                            headers,
                            owner_repo,
                            run_id,
                        )
                        if conclusion in FAILED_CONCLUSIONS
                        else []
                    )
                    _log.info("ci_watcher_lookback_hit", run_id=run_id, conclusion=conclusion)
                    return {"run_id": run_id, "conclusion": conclusion, "failed_jobs": failed_jobs}

                # Phase 2: Poll for active runs
                _log.info("ci_watcher_polling", branch=branch, repo=owner_repo)
                attempt = 0
                found_run: dict[str, Any] | None = None
                while time.monotonic() < deadline:
                    active = await self._fetch_active_runs(
                        client,
                        headers,
                        owner_repo,
                        branch,
                        scope,
                    )
                    if active:
                        found_run = active[0]
                        break
                    # Also re-check completed in case it finished between phases
                    completed = await self._fetch_completed_runs(
                        client,
                        headers,
                        owner_repo,
                        branch,
                        scope,
                        lookback_seconds,
                    )
                    if completed:
                        run = completed[0]
                        run_id = run["id"]
                        conclusion = run.get("conclusion", "unknown")
                        failed_jobs = (
                            await self._fetch_failed_jobs(
                                client,
                                headers,
                                owner_repo,
                                run_id,
                            )
                            if conclusion in FAILED_CONCLUSIONS
                            else []
                        )
                        return {
                            "run_id": run_id,
                            "conclusion": conclusion,
                            "failed_jobs": failed_jobs,
                        }

                    sleep_duration = _jittered_sleep(attempt)
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        break
                    await asyncio.sleep(min(sleep_duration, remaining))
                    attempt += 1

                if found_run is None:
                    _log.warning("ci_watcher_no_runs", branch=branch, repo=owner_repo)
                    return {"run_id": None, "conclusion": "no_runs", "failed_jobs": []}

                # Phase 3: Wait for the found run to complete
                run_id = found_run["id"]
                _log.info("ci_watcher_waiting", run_id=run_id)
                attempt = 0
                while time.monotonic() < deadline:
                    run_data = await self._poll_run_status(
                        client,
                        headers,
                        owner_repo,
                        run_id,
                    )
                    if run_data.get("status") == "completed":
                        conclusion = run_data.get("conclusion", "unknown")
                        failed_jobs = (
                            await self._fetch_failed_jobs(
                                client,
                                headers,
                                owner_repo,
                                run_id,
                            )
                            if conclusion in FAILED_CONCLUSIONS
                            else []
                        )
                        _log.info("ci_watcher_completed", run_id=run_id, conclusion=conclusion)
                        return {
                            "run_id": run_id,
                            "conclusion": conclusion,
                            "failed_jobs": failed_jobs,
                        }

                    sleep_duration = _jittered_sleep(attempt)
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        break
                    await asyncio.sleep(min(sleep_duration, remaining))
                    attempt += 1

                _log.warning("ci_watcher_timeout", run_id=run_id, timeout=timeout_seconds)
                return {"run_id": run_id, "conclusion": "timed_out", "failed_jobs": []}

        except httpx.HTTPStatusError as exc:
            _log.warning("ci_watcher_http_error", status=exc.response.status_code, branch=branch)
            return {
                "run_id": None,
                "conclusion": "error",
                "failed_jobs": [],
                "error": f"HTTP {exc.response.status_code}: {exc.response.text[:200]}",
            }
        except httpx.RequestError as exc:
            _log.warning("ci_watcher_request_error", branch=branch, error=str(exc))
            return {
                "run_id": None,
                "conclusion": "error",
                "failed_jobs": [],
                "error": f"Request error: {exc}",
            }

    async def status(
        self,
        branch: str,
        *,
        repo: str | None = None,
        run_id: int | None = None,
        scope: CIRunScope = CIRunScope(),
        cwd: str = "",
    ) -> dict[str, Any]:
        """Return current CI status without waiting.

        Returns dict with "runs" list, each containing id, status,
        conclusion, and failed_jobs. Never raises.
        """
        owner_repo = await self._resolve_repo(repo, cwd)
        if not owner_repo:
            return {"runs": [], "error": "Could not determine repository."}

        headers = self._headers()

        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(15.0, connect=5.0)) as client:
                if run_id is not None:
                    run_data = await self._poll_run_status(
                        client,
                        headers,
                        owner_repo,
                        run_id,
                    )
                    conclusion = run_data.get("conclusion")
                    failed_jobs = (
                        await self._fetch_failed_jobs(
                            client,
                            headers,
                            owner_repo,
                            run_id,
                        )
                        if conclusion in FAILED_CONCLUSIONS
                        else []
                    )
                    return {
                        "runs": [
                            {
                                "id": run_id,
                                "status": run_data.get("status", "unknown"),
                                "conclusion": conclusion,
                                "failed_jobs": failed_jobs,
                            }
                        ]
                    }

                url = f"https://api.github.com/repos/{owner_repo}/actions/runs"
                params: dict[str, str | int] = {"branch": branch, "per_page": 5}
                if scope.workflow:
                    params["workflow_id"] = scope.workflow
                resp = await client.get(url, headers=headers, params=params)
                resp.raise_for_status()
                data = resp.json()

                runs = []
                for r in data.get("workflow_runs", [])[:5]:
                    r_conclusion = r.get("conclusion")
                    failed_jobs = (
                        await self._fetch_failed_jobs(
                            client,
                            headers,
                            owner_repo,
                            r["id"],
                        )
                        if r_conclusion in FAILED_CONCLUSIONS
                        else []
                    )
                    runs.append(
                        {
                            "id": r["id"],
                            "status": r.get("status", "unknown"),
                            "conclusion": r_conclusion,
                            "failed_jobs": failed_jobs,
                        }
                    )
                return {"runs": runs}

        except httpx.HTTPStatusError as exc:
            _log.warning("ci_status_http_error", status=exc.response.status_code)
            return {
                "runs": [],
                "error": f"HTTP {exc.response.status_code}: {exc.response.text[:200]}",
            }
        except httpx.RequestError as exc:
            _log.warning("ci_status_request_error", error=str(exc))
            return {"runs": [], "error": f"Request error: {exc}"}
