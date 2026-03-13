"""Merge queue polling service (L1) — monitors GitHub merge queue for a PR.

Never raises. All errors are returned as structured results.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

import httpx

from autoskillit.core import get_logger

_log = get_logger(__name__)

_GRAPHQL_ENDPOINT = "https://api.github.com/graphql"
_REST_ENDPOINT = "https://api.github.com"

_QUEUE_ENTRIES_QUERY = """\
query GetMergeQueueEntries($owner: String!, $repo: String!, $branch: String!) {
  repository(owner: $owner, name: $repo) {
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


class DefaultMergeQueueWatcher:
    """Polls GitHub merge queue state until merged, ejected, or timed out.

    Uses GitHub REST API for PR state and GraphQL API for queue entries.
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
        repo: str | None = None,  # "owner/name" format
        cwd: str = ".",
        timeout_seconds: int = 600,
        poll_interval: int = 15,
    ) -> dict[str, Any]:
        """Poll until PR is merged, ejected, or timeout expires.

        Returns:
            {"success": bool, "pr_state": "merged"|"ejected"|"timeout"|"error", "reason": str}
        """
        if not repo or "/" not in repo:
            return {
                "success": False,
                "pr_state": "error",
                "reason": f"Invalid repo format: {repo!r}. Expected 'owner/name'.",
            }
        owner, repo_name = repo.split("/", 1)

        deadline = time.monotonic() + timeout_seconds
        stuck_cycles = 0

        while time.monotonic() < deadline:
            try:
                pr_info = await self._fetch_pr_state(pr_number, owner, repo_name)
            except Exception as exc:
                _log.warning("merge_queue.pr_state_error", pr_number=pr_number, exc=str(exc))
                await asyncio.sleep(poll_interval)
                continue

            if pr_info.get("merged"):
                return {"success": True, "pr_state": "merged", "reason": "PR merged successfully"}

            if pr_info.get("state") == "closed":
                return {
                    "success": False,
                    "pr_state": "ejected",
                    "reason": "PR was closed without merging",
                }

            try:
                entries = await self._fetch_queue_entries(owner, repo_name, target_branch)
            except Exception as exc:
                _log.warning("merge_queue.entries_error", exc=str(exc))
                entries = []

            entry = next((e for e in entries if e.get("pr_number") == pr_number), None)

            if entry is not None:
                if entry.get("state") == "UNMERGEABLE":
                    return {
                        "success": False,
                        "pr_state": "ejected",
                        "reason": "PR is UNMERGEABLE in merge queue",
                    }
                await asyncio.sleep(poll_interval)
                continue

            # PR is open, not in queue — check for stuck condition
            if self._is_stuck(pr_info):
                if stuck_cycles < 1:
                    _log.info("merge_queue.stuck_detected", pr_number=pr_number)
                    try:
                        await self._toggle_auto_merge(pr_number, owner, repo_name)
                    except Exception as exc:
                        _log.warning("merge_queue.toggle_error", exc=str(exc))
                    stuck_cycles += 1
                await asyncio.sleep(poll_interval)
                continue
            # Not stuck → ejected
            return {
                "success": False,
                "pr_state": "ejected",
                "reason": "PR was ejected from merge queue (not in queue and not merged)",
            }

        return {
            "success": False,
            "pr_state": "timeout",
            "reason": f"Timed out after {timeout_seconds}s waiting for PR #{pr_number}",
        }

    @staticmethod
    def _is_stuck(pr_info: dict[str, Any]) -> bool:
        """Stuck = auto-merge is set AND merge is clean — queue should have picked it up."""
        return pr_info.get("auto_merge") is not None and pr_info.get("mergeable_state") in {
            "clean",
            "has_hooks",
        }

    async def _fetch_pr_state(self, pr_number: int, owner: str, repo: str) -> dict[str, Any]:
        resp = await self._client.get(
            f"{_REST_ENDPOINT}/repos/{owner}/{repo}/pulls/{pr_number}",
        )
        resp.raise_for_status()
        data = resp.json()
        return {
            "state": data["state"],
            "merged": data.get("merged", False),
            "auto_merge": data.get("auto_merge"),
            "mergeable_state": data.get("mergeable_state"),
        }

    async def _fetch_queue_entries(
        self, owner: str, repo: str, branch: str
    ) -> list[dict[str, Any]]:
        resp = await self._client.post(
            _GRAPHQL_ENDPOINT,
            json={
                "query": _QUEUE_ENTRIES_QUERY,
                "variables": {"owner": owner, "repo": repo, "branch": branch},
            },
        )
        resp.raise_for_status()
        payload = resp.json()
        nodes = (
            payload.get("data", {})
            .get("repository", {})
            .get("mergeQueue", {})
            .get("entries", {})
            .get("nodes", [])
        )
        return [
            {"pr_number": n["pullRequest"]["number"], "state": n["state"]}
            for n in nodes
            if n.get("pullRequest") and n["pullRequest"].get("number") is not None
        ]

    async def _toggle_auto_merge(self, pr_number: int, owner: str, repo: str) -> None:
        url = f"{_REST_ENDPOINT}/repos/{owner}/{repo}/pulls/{pr_number}/auto-merge"
        resp = await self._client.delete(url)
        resp.raise_for_status()
        await asyncio.sleep(2)
        resp = await self._client.put(url, json={"merge_method": "squash"})
        resp.raise_for_status()
