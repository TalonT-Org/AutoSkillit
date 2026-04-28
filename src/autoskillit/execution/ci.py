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
import urllib.parse
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

import httpx

from autoskillit.core import CIRunScope, get_logger
from autoskillit.execution.github import github_headers, make_tracked_httpx_client

if TYPE_CHECKING:
    from autoskillit.core._type_protocols import GitHubApiLog

logger = get_logger(__name__)

# Backoff schedule: (floor, ceiling) per attempt band.
_BACKOFF_BANDS: tuple[tuple[int, int], ...] = ((5, 10), (10, 20), (15, 30))

# All GitHub Actions check run conclusion values known to be returned by the REST API.
# https://docs.github.com/en/rest/checks/runs
KNOWN_CI_CONCLUSIONS: frozenset[str] = frozenset(
    {
        "success",
        "failure",
        "neutral",
        "cancelled",
        "skipped",
        "timed_out",
        "action_required",
        "startup_failure",
        "stale",
    }
)

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
    """Full-jitter exponential backoff with per-attempt floor bands."""
    floor, ceiling = _BACKOFF_BANDS[min(attempt, len(_BACKOFF_BANDS) - 1)]
    return random.uniform(floor, ceiling)


def _validate_run_matches_scope(run: dict[str, Any], scope: CIRunScope) -> bool:
    """Verify a run returned by the API matches the requested scope fields.

    This is a defense-in-depth check: the API query params should filter
    server-side, but this client-side validation catches any discrepancy.
    Each scope field is only checked when it is not None (i.e., was requested).
    """
    if scope.event and run.get("event") != scope.event:
        return False
    if scope.head_sha and run.get("head_sha") != scope.head_sha:
        return False
    return True


class DefaultCIWatcher:
    """Concrete CI watcher using GitHub REST API via httpx.

    Implements the CIWatcher protocol.
    Never raises — errors are returned as structured dicts.
    """

    _UNRESOLVED = object()

    def __init__(
        self,
        *,
        token: str | None | Callable[[], str | None] = None,
        tracker: GitHubApiLog | None = None,
    ) -> None:
        self._token_factory: Callable[[], str | None] | None
        if callable(token):
            self._token_factory = token
            self._token: str | None = self._UNRESOLVED  # type: ignore[assignment]
        else:
            self._token_factory = None
            self._token = token
        self._tracker = tracker
        self._etag_cache: dict[str, tuple[str, Any]] = {}  # url -> (etag, cached_json)

    def _resolve_token(self) -> str | None:
        if self._token is self._UNRESOLVED:
            self._token = self._token_factory() if self._token_factory is not None else None
        return self._token

    def _headers(self) -> dict[str, str]:
        return github_headers(self._resolve_token())

    async def _get_with_etag(
        self,
        client: httpx.AsyncClient,
        headers: dict[str, str],
        url: str,
        params: dict[str, str | int] | None = None,
    ) -> Any:
        """GET with ETag conditional request support.

        Returns parsed JSON. On 304 (Not Modified), returns cached body.
        On 200, stores ETag + body for future conditional requests.
        """
        cache_key = url + (
            "?" + urllib.parse.urlencode(sorted((str(k), str(v)) for k, v in params.items()))
            if params
            else ""
        )
        req_headers = dict(headers)
        cached = self._etag_cache.get(cache_key)
        if cached:
            req_headers["If-None-Match"] = cached[0]

        resp = await client.get(url, headers=req_headers, params=params)
        if resp.status_code == 304:
            if cached:
                return cached[1]
            raise RuntimeError(
                f"Server returned 304 Not Modified for {url!r} "
                "but no cached response exists for this URL. "
                "This indicates a server-side inconsistency."
            )
        resp.raise_for_status()
        data = resp.json()

        etag = resp.headers.get("ETag")
        if etag:
            self._etag_cache[cache_key] = (etag, data)
        return data

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
        cutoff_dt: datetime | None,
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
        if scope.event:
            params["event"] = scope.event

        data = await self._get_with_etag(client, headers, url, params)

        # SHA is a content-addressable identifier — time is irrelevant when identity is known
        if scope.head_sha:
            return list(data.get("workflow_runs", []))

        runs = []
        for run in data.get("workflow_runs", []):
            updated = run.get("updated_at", "")
            if updated:
                try:
                    run_time = datetime.fromisoformat(updated.replace("Z", "+00:00"))
                    if cutoff_dt is None or run_time >= cutoff_dt:
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
        if scope.event:
            params["event"] = scope.event

        data = await self._get_with_etag(client, headers, url, params)

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
        _start_time = time.monotonic()
        deadline = _start_time + timeout_seconds
        cutoff_dt = datetime.now(UTC) - timedelta(seconds=lookback_seconds)

        try:
            async with make_tracked_httpx_client(
                self._tracker,
                timeout=httpx.Timeout(15.0, connect=5.0),
                headers=github_headers(self._resolve_token()),
            ) as client:
                # Phase 1: Look-back — check for recently-completed runs
                logger.info(
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
                    cutoff_dt,
                )
                if completed:
                    valid_completed = [
                        r for r in completed if _validate_run_matches_scope(r, scope)
                    ]
                    if not valid_completed:
                        logger.warning(
                            "ci_watcher_scope_mismatch",
                            count=len(completed),
                            scope=str(scope),
                        )
                    else:
                        run = valid_completed[0]
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
                        logger.info(
                            "ci_watcher_lookback_hit", run_id=run_id, conclusion=conclusion
                        )
                        return {
                            "run_id": run_id,
                            "conclusion": conclusion,
                            "failed_jobs": failed_jobs,
                        }

                # Phase 2: Poll for active runs
                logger.info("ci_watcher_polling", branch=branch, repo=owner_repo)
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
                        valid_active = [r for r in active if _validate_run_matches_scope(r, scope)]
                        if valid_active:
                            found_run = valid_active[0]
                            break
                    # Also re-check completed in case it finished between phases
                    completed = await self._fetch_completed_runs(
                        client,
                        headers,
                        owner_repo,
                        branch,
                        scope,
                        cutoff_dt,
                    )
                    if completed:
                        valid_completed = [
                            r for r in completed if _validate_run_matches_scope(r, scope)
                        ]
                        if valid_completed:
                            run = valid_completed[0]
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
                    poll_duration = time.monotonic() - _start_time
                    logger.warning(
                        "ci_watcher_no_runs",
                        branch=branch,
                        repo=owner_repo,
                        scope_event=scope.event,
                        scope_workflow=scope.workflow,
                        scope_head_sha=scope.head_sha,
                        poll_duration_s=round(poll_duration, 1),
                    )
                    return {
                        "run_id": None,
                        "conclusion": "no_runs",
                        "failed_jobs": [],
                        "branch": branch,
                        "poll_duration_s": round(poll_duration, 1),
                        "scope_used": {
                            "event": scope.event,
                            "workflow": scope.workflow,
                            "head_sha": scope.head_sha,
                        },
                    }

                # Phase 3: Wait for the found run to complete
                run_id = found_run["id"]
                logger.info("ci_watcher_waiting", run_id=run_id)
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
                        logger.info("ci_watcher_completed", run_id=run_id, conclusion=conclusion)
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

                logger.warning("ci_watcher_timeout", run_id=run_id, timeout=timeout_seconds)
                return {
                    "run_id": run_id,
                    "conclusion": "timed_out",
                    "failed_jobs": [],
                    "run_status": run_data.get("status", "in_progress"),
                    "hint": (
                        f"CI run {run_id} is still in progress (not failed). "
                        "Call wait_for_ci again to continue watching."
                    ),
                }

        except httpx.HTTPStatusError as exc:
            logger.warning("ci_watcher_http_error", status=exc.response.status_code, branch=branch)
            return {
                "run_id": None,
                "conclusion": "error",
                "failed_jobs": [],
                "error": f"HTTP {exc.response.status_code}: {exc.response.text[:200]}",
            }
        except httpx.RequestError as exc:
            logger.warning("ci_watcher_request_error", branch=branch, error=str(exc))
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
            async with make_tracked_httpx_client(
                self._tracker,
                timeout=httpx.Timeout(15.0, connect=5.0),
                headers=github_headers(self._resolve_token()),
            ) as client:
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
            logger.warning("ci_status_http_error", status=exc.response.status_code)
            return {
                "runs": [],
                "error": f"HTTP {exc.response.status_code}: {exc.response.text[:200]}",
            }
        except httpx.RequestError as exc:
            logger.warning("ci_status_request_error", error=str(exc))
            return {"runs": [], "error": f"Request error: {exc}"}
