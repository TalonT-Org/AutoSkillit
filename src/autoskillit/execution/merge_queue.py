"""Merge queue polling service (L1) — monitors GitHub merge queue for a PR.

Never raises. All errors are returned as structured results.
"""

from __future__ import annotations

import asyncio
import time
from datetime import datetime, timezone
from typing import Any

import httpx

from autoskillit.core import get_logger

_log = get_logger(__name__)

_GRAPHQL_ENDPOINT = "https://api.github.com/graphql"

_QUERY = """
query GetPRAndQueueState($owner: String!, $repo: String!, $prNumber: Int!, $branch: String!) {
  repository(owner: $owner, name: $repo) {
    pullRequest(number: $prNumber) {
      id
      merged
      state
      mergeStateStatus
      autoMergeRequest {
        enabledAt
      }
    }
    mergeQueue(branch: $branch) {
      entries(first: 100) {
        nodes {
          pullRequest { number }
          state
        }
      }
    }
  }
}
"""

_MUTATION_DISABLE_AUTO_MERGE = """
mutation DisableAutoMerge($prId: ID!) {
  disablePullRequestAutoMerge(input: {pullRequestId: $prId}) {
    pullRequest { number }
  }
}
"""

_MUTATION_ENABLE_AUTO_MERGE = """
mutation EnableAutoMerge($prId: ID!, $mergeMethod: PullRequestMergeMethod!) {
  enablePullRequestAutoMerge(input: {pullRequestId: $prId, mergeMethod: $mergeMethod}) {
    pullRequest { number }
  }
}
"""


class DefaultMergeQueueWatcher:
    """Polls GitHub merge queue state until merged, ejected, stalled, or timed out.

    Uses a single consolidated GraphQL query per poll cycle to atomically capture
    both PR state and queue entries, eliminating the race condition where two
    separate API calls straddle a merge event.

    Never raises; all errors are returned as structured dicts.
    """

    def __init__(self, token: str | None) -> None:
        headers: dict[str, str] = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        if token:
            headers["Authorization"] = f"Bearer {token}"
        self._client = httpx.AsyncClient(
            headers=headers,
            limits=httpx.Limits(keepalive_expiry=60),
            timeout=30.0,
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    async def wait(
        self,
        pr_number: int,
        target_branch: str,
        repo: str | None = None,
        cwd: str = ".",
        timeout_seconds: int = 600,
        poll_interval: int = 15,
        stall_grace_period: int = 60,
        max_stall_retries: int = 3,
    ) -> dict[str, Any]:
        """Poll until PR is merged, ejected, stalled, or timeout expires.

        Args:
            pr_number: PR number to monitor.
            target_branch: Branch the merge queue targets.
            repo: "owner/name" format. Required.
            cwd: Working directory (unused; retained for interface compatibility).
            timeout_seconds: Maximum polling duration (default 600s).
            poll_interval: Seconds between poll cycles (default 15s).
            stall_grace_period: Seconds after autoMergeRequest.enabledAt before stall
                recovery may trigger (default 60s). Prevents intervention during normal
                queue processing.
            max_stall_retries: Maximum disable/re-enable toggle attempts before
                returning pr_state="stalled" (default 3).

        Returns:
            {
                "success": bool,
                "pr_state": "merged" | "ejected" | "stalled" | "timeout" | "error",
                "reason": str,
                "stall_retries_attempted": int,
            }
        """
        if not repo or "/" not in repo:
            return {
                "success": False,
                "pr_state": "error",
                "reason": f"Invalid repo format: {repo!r}. Expected 'owner/name'.",
                "stall_retries_attempted": 0,
            }
        owner, repo_name = repo.split("/", 1)

        deadline = time.monotonic() + timeout_seconds
        stall_retries_attempted: int = 0
        not_in_queue_cycles: int = 0

        def _make_result(success: bool, pr_state: str, reason: str) -> dict[str, Any]:
            return {
                "success": success,
                "pr_state": pr_state,
                "reason": reason,
                "stall_retries_attempted": stall_retries_attempted,
            }

        while time.monotonic() < deadline:
            try:
                state = await self._fetch_pr_and_queue_state(
                    pr_number, owner, repo_name, target_branch
                )
            except Exception:
                _log.warning("fetch_pr_and_queue_state failed, retrying", exc_info=True)
                await asyncio.sleep(poll_interval)
                continue

            # Terminal: merged
            if state["merged"]:
                return _make_result(True, "merged", "PR merged successfully")

            # Terminal: closed without merge
            if state["state"] == "CLOSED":
                return _make_result(False, "ejected", "PR was closed without merging")

            # In queue
            if state["in_queue"]:
                not_in_queue_cycles = 0  # reset confirmation window
                if state["queue_state"] == "UNMERGEABLE":
                    return _make_result(False, "ejected", "PR is UNMERGEABLE in merge queue")
                await asyncio.sleep(poll_interval)
                continue

            # Not in queue — Bug 1: confirmation window
            not_in_queue_cycles += 1
            if not_in_queue_cycles < 2:
                # One extra cycle: gives time for merge-in-progress to reflect merged=true
                await asyncio.sleep(poll_interval)
                continue

            # Confirmed: not in queue for 2+ cycles — check stall
            enabled_at = state["auto_merge_enabled_at"]
            merge_status = state["merge_state_status"]
            now = datetime.now(timezone.utc)

            is_stall_candidate = (
                enabled_at is not None and merge_status in {"CLEAN", "HAS_HOOKS"}
            )

            if is_stall_candidate:
                stall_duration = (now - enabled_at).total_seconds()

                if stall_duration < stall_grace_period:
                    # Within grace period — wait without intervening
                    await asyncio.sleep(poll_interval)
                    continue

                # Grace expired: attempt toggle if budget remains
                if stall_retries_attempted < max_stall_retries:
                    backoff = min(30 * (2**stall_retries_attempted), 120)
                    _log.info(
                        "merge_queue_stall_detected",
                        stall_duration=stall_duration,
                        attempt=stall_retries_attempted + 1,
                        backoff=backoff,
                    )
                    try:
                        await self._toggle_auto_merge(state["pr_node_id"])
                    except Exception:
                        _log.warning("toggle_auto_merge failed", exc_info=True)
                    stall_retries_attempted += 1
                    not_in_queue_cycles = 0
                    await asyncio.sleep(backoff)
                    continue

                # Budget exhausted — return distinct stalled state
                return _make_result(
                    False,
                    "stalled",
                    f"PR #{pr_number} stall unresolved after {max_stall_retries} toggle attempts",
                )

            # Not a stall (no auto_merge or bad merge state) and confirmed absent — genuine ejection
            return _make_result(
                False,
                "ejected",
                "PR was ejected from merge queue (not in queue and not merged)",
            )

        # Deadline exceeded
        return _make_result(
            False,
            "timeout",
            f"Timed out after {timeout_seconds}s waiting for PR #{pr_number}",
        )

    async def _fetch_pr_and_queue_state(
        self, pr_number: int, owner: str, repo: str, target_branch: str
    ) -> dict[str, Any]:
        """Single GraphQL round-trip returning PR state and merge queue entries."""
        variables = {
            "owner": owner,
            "repo": repo,
            "prNumber": pr_number,
            "branch": target_branch,
        }
        resp = await self._client.post(
            _GRAPHQL_ENDPOINT, json={"query": _QUERY, "variables": variables}
        )
        resp.raise_for_status()
        data = resp.json()
        if "errors" in data:
            raise RuntimeError(f"GraphQL error: {data['errors']}")

        repo_data = data["data"]["repository"]
        pr = repo_data["pullRequest"]
        entries = repo_data.get("mergeQueue", {}) or {}
        nodes = (entries.get("entries") or {}).get("nodes") or []

        auto_merge = pr.get("autoMergeRequest") or {}
        enabled_at_raw = auto_merge.get("enabledAt")
        enabled_at: datetime | None = None
        if enabled_at_raw:
            enabled_at = datetime.fromisoformat(enabled_at_raw.replace("Z", "+00:00"))

        queue_entry = next(
            (n for n in nodes if n["pullRequest"]["number"] == pr_number), None
        )
        return {
            "merged": pr["merged"],
            "state": pr["state"],
            "merge_state_status": pr.get("mergeStateStatus", "UNKNOWN"),
            "auto_merge_enabled_at": enabled_at,
            "pr_node_id": pr["id"],
            "in_queue": queue_entry is not None,
            "queue_state": queue_entry["state"] if queue_entry else None,
        }

    async def _toggle_auto_merge(self, pr_node_id: str) -> None:
        """Disable then re-enable auto-merge via GraphQL mutations."""
        for mutation, variables in [
            (_MUTATION_DISABLE_AUTO_MERGE, {"prId": pr_node_id}),
            (_MUTATION_ENABLE_AUTO_MERGE, {"prId": pr_node_id, "mergeMethod": "SQUASH"}),
        ]:
            resp = await self._client.post(
                _GRAPHQL_ENDPOINT, json={"query": mutation, "variables": variables}
            )
            resp.raise_for_status()
            body = resp.json()
            if "errors" in body:
                raise RuntimeError(f"GraphQL mutation error: {body['errors']}")
        await asyncio.sleep(2)
