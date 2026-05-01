"""Merge queue polling service (IL-1) — monitors GitHub merge queue for a PR.

Never raises. All errors are returned as structured results.

Facade: re-exports from _merge_queue_classifier and _merge_queue_repo_state.
"""

from __future__ import annotations

import asyncio
import random  # noqa: F401 — re-exported for test monkeypatching (_mq.random.uniform)
import re
import time
from collections.abc import Callable
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, assert_never

import httpx

from autoskillit.core import PRState, get_logger
from autoskillit.execution._merge_queue_classifier import (
    _QUERY_FIELD_MAP,
    KNOWN_MQ_MERGE_STATE_STATUSES,  # noqa: F401 — re-export for callers
    CIStillRunning,
    ClassificationResult,  # noqa: F401 — re-export for callers
    ClassifierInconclusive,  # noqa: F401 — re-export for callers
    NoPositiveSignal,
    PRFetchState,
    _classify_pr_state,
    _is_not_enrolled,  # noqa: F401 — re-export for callers
    _is_positive_dropped_healthy,  # noqa: F401 — re-export for callers
    _is_positive_stall,  # noqa: F401 — re-export for callers
)
from autoskillit.execution._merge_queue_repo_state import (
    _GRAPHQL_ENDPOINT,
    _RATE_LIMIT_MAX_ATTEMPTS,  # noqa: F401 — re-export for callers
    _RATE_LIMIT_SECONDARY_MARKER,  # noqa: F401 — re-export for callers
    _REPO_STATE_QUERY,  # noqa: F401 — re-export for callers
    _has_merge_group_trigger,  # noqa: F401 — re-export for callers
    _is_secondary_rate_limit,  # noqa: F401 — re-export for callers
    _push_trigger_applies_to_branch,  # noqa: F401 — re-export for callers
    _retry_after_seconds,  # noqa: F401 — re-export for callers
    _text_has_push_trigger,  # noqa: F401 — re-export for callers
    fetch_repo_merge_state,  # noqa: F401 — re-export for callers
)
from autoskillit.execution.github import github_headers, make_tracked_httpx_client

if TYPE_CHECKING:
    from autoskillit.core._type_protocols_logging import GitHubApiLog

logger = get_logger(__name__)

_QUERY = """
query GetPRAndQueueState($owner: String!, $repo: String!, $prNumber: Int!, $branch: String!) {
  repository(owner: $owner, name: $repo) {
    pullRequest(number: $prNumber) {
      id
      merged
      state
      mergeable
      mergeStateStatus
      autoMergeRequest {
        enabledAt
      }
      statusCheckRollup {
        state
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

# Part 2 of the _QUERY_FIELD_MAP validation: checks that every non-computed field
# path head appears as a GraphQL field name in _QUERY.
# Part 1 (keys match PRFetchState) lives in _merge_queue_classifier.py.
for _key, _path in _QUERY_FIELD_MAP.items():
    if _path.startswith("<"):
        continue
    _head = _path.split(".", 1)[0]
    # Word-boundary search prevents "state" from matching inside "mergeStateStatus".
    if not re.search(r"\b" + re.escape(_head) + r"\b", _QUERY):
        raise RuntimeError(
            f"_QUERY is missing GraphQL field {_head!r} required by PRFetchState[{_key!r}]"
        )

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

_MUTATION_ENQUEUE_PR = """
mutation EnqueuePullRequest($prId: ID!) {
  enqueuePullRequest(input: {pullRequestId: $prId}) {
    mergeQueueEntry { id }
  }
}
"""


class DefaultMergeQueueWatcher:
    """Polls GitHub merge queue state until merged, ejected, stalled, or timed out.

    Never raises; all errors are returned as structured dicts.
    """

    def __init__(
        self,
        token: str | None | Callable[[], str | None],
        *,
        tracker: GitHubApiLog | None = None,
    ) -> None:
        self._token_factory: Callable[[], str | None] | None
        self._tracker = tracker
        if callable(token):
            self._token_factory = token
            self._client: httpx.AsyncClient | None = None
        else:
            self._token_factory = None
            self._client = make_tracked_httpx_client(
                self._tracker,
                timeout=httpx.Timeout(30.0),
                headers=github_headers(token),
                limits=httpx.Limits(keepalive_expiry=60),
            )

    def _ensure_client(self) -> httpx.AsyncClient:
        if self._client is None:
            resolved = self._token_factory() if self._token_factory is not None else None
            self._client = make_tracked_httpx_client(
                self._tracker,
                timeout=httpx.Timeout(30.0),
                headers=github_headers(resolved),
                limits=httpx.Limits(keepalive_expiry=60),
            )
        return self._client

    async def aclose(self) -> None:
        if self._client is not None:
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
        not_in_queue_confirmation_cycles: int = 2,
        max_inconclusive_retries: int = 5,
        auto_merge_available: bool = True,
    ) -> dict[str, Any]:
        """Poll until PR is merged, ejected, stalled, dropped, or timeout expires.

        Returns {"success": bool, "pr_state": str, "reason": str, "stall_retries_attempted": int}.
        """
        if not repo or "/" not in repo:
            return {
                "success": False,
                "pr_state": PRState.ERROR.value,
                "reason": f"Invalid repo format: {repo!r}. Expected 'owner/name'.",
                "stall_retries_attempted": 0,
            }
        owner, repo_name = repo.split("/", 1)

        deadline = time.monotonic() + timeout_seconds
        stall_retries_attempted: int = 0
        not_in_queue_cycles: int = 0
        inconclusive_count: int = 0
        ever_enrolled: bool = False

        def _make_result(
            success: bool, pr_state: PRState, reason: str, ejection_cause: str = ""
        ) -> dict[str, Any]:
            result: dict[str, Any] = {
                "success": success,
                "pr_state": pr_state.value,
                "reason": reason,
                "stall_retries_attempted": stall_retries_attempted,
            }
            if ejection_cause:
                result["ejection_cause"] = ejection_cause
            return result

        while time.monotonic() < deadline:
            try:
                state = await self._fetch_pr_and_queue_state(
                    pr_number, owner, repo_name, target_branch
                )
            except Exception:
                logger.warning("fetch_pr_and_queue_state failed, retrying", exc_info=True)
                await asyncio.sleep(poll_interval)
                continue

            if state["in_queue"] or state["auto_merge_present"]:
                ever_enrolled = True

            if state["in_queue"]:
                not_in_queue_cycles = 0
                inconclusive_count = 0
                if state["queue_state"] == "UNMERGEABLE":
                    return _make_result(False, PRState.EJECTED, "PR is UNMERGEABLE in merge queue")
                await asyncio.sleep(poll_interval)
                continue

            not_in_queue_cycles += 1

            try:
                classification = _classify_pr_state(state, ever_enrolled=ever_enrolled)
            except CIStillRunning:
                await asyncio.sleep(poll_interval)
                continue
            except NoPositiveSignal as exc:
                if not_in_queue_cycles < not_in_queue_confirmation_cycles:
                    await asyncio.sleep(poll_interval)
                    continue
                inconclusive_count += 1
                if inconclusive_count >= max_inconclusive_retries:
                    return _make_result(
                        False,
                        PRState.TIMEOUT,
                        f"Inconclusive after {max_inconclusive_retries} retries: {exc.reason}",
                    )
                await asyncio.sleep(poll_interval)
                continue

            if classification.terminal == PRState.MERGED:
                return _make_result(True, PRState.MERGED, classification.reason)

            if state["state"] == "CLOSED":
                ejection_cause = (
                    "ci_failure" if classification.terminal == PRState.EJECTED_CI_FAILURE else ""
                )
                return _make_result(
                    False, classification.terminal, classification.reason, ejection_cause
                )

            if not_in_queue_cycles < not_in_queue_confirmation_cycles:
                await asyncio.sleep(poll_interval)
                continue

            if classification.terminal == PRState.STALLED:
                enabled_at = state["auto_merge_enabled_at"]
                assert enabled_at is not None  # guaranteed by _is_positive_stall
                now = datetime.now(UTC)
                stall_duration = max(0.0, (now - enabled_at).total_seconds())
                if stall_duration < stall_grace_period:
                    await asyncio.sleep(poll_interval)
                    continue
                if stall_retries_attempted < max_stall_retries:
                    backoff = min(30 * (2**stall_retries_attempted), 120)
                    logger.info(
                        "merge_queue_stall_detected",
                        stall_duration=stall_duration,
                        attempt=stall_retries_attempted + 1,
                        backoff=backoff,
                    )
                    try:
                        await self._toggle_auto_merge(
                            state["pr_node_id"],
                            auto_merge_available=auto_merge_available,
                        )
                    except Exception:
                        logger.warning("toggle_auto_merge failed", exc_info=True)
                    stall_retries_attempted += 1
                    not_in_queue_cycles = 0
                    await asyncio.sleep(backoff)
                    continue
                return _make_result(
                    False,
                    PRState.STALLED,
                    f"PR #{pr_number} stall unresolved after {max_stall_retries} toggle attempts",
                )

            elif classification.terminal == PRState.EJECTED_CI_FAILURE:
                return _make_result(
                    False,
                    PRState.EJECTED_CI_FAILURE,
                    classification.reason,
                    ejection_cause="ci_failure",
                )

            elif classification.terminal == PRState.EJECTED:
                return _make_result(False, PRState.EJECTED, classification.reason)

            elif classification.terminal == PRState.DROPPED_HEALTHY:
                return _make_result(False, PRState.DROPPED_HEALTHY, classification.reason)

            elif classification.terminal == PRState.NOT_ENROLLED:
                return _make_result(False, PRState.NOT_ENROLLED, classification.reason)

            else:
                # Unreachable: _classify_pr_state never returns TIMEOUT or ERROR.
                # The assert_never call provides static exhaustiveness for future
                # additions to PRState (pyright/mypy will flag any new unhandled member).
                assert_never(classification.terminal)  # type: ignore[arg-type]

        return _make_result(
            False,
            PRState.TIMEOUT,
            f"Timed out after {timeout_seconds}s waiting for PR #{pr_number}",
        )

    async def _fetch_pr_and_queue_state(
        self, pr_number: int, owner: str, repo: str, target_branch: str
    ) -> PRFetchState:
        """Single GraphQL round-trip returning PR state and merge queue entries."""
        variables = {
            "owner": owner,
            "repo": repo,
            "prNumber": pr_number,
            "branch": target_branch,
        }
        resp = await self._ensure_client().post(
            _GRAPHQL_ENDPOINT, json={"query": _QUERY, "variables": variables}
        )
        resp.raise_for_status()
        data = resp.json()
        if "errors" in data:
            raise RuntimeError(f"GraphQL error: {data['errors']}")

        if not data.get("data") or data["data"] is None or "repository" not in data["data"]:
            raise RuntimeError(f"GraphQL response missing 'repository' key: {str(data)[:200]}")
        repo_data = data["data"]["repository"]
        pr = repo_data["pullRequest"]
        if pr is None:
            raise RuntimeError(f"GraphQL returned null pullRequest for PR #{pr_number}")
        entries_raw = repo_data.get("mergeQueue") or {}
        entries = entries_raw if isinstance(entries_raw, dict) else {}
        nodes_raw = (entries.get("entries") or {}).get("nodes")
        nodes = nodes_raw if isinstance(nodes_raw, list) else []

        auto_merge_raw = pr.get("autoMergeRequest")
        auto_merge_present: bool = auto_merge_raw is not None
        auto_merge = auto_merge_raw or {}
        enabled_at_raw = auto_merge.get("enabledAt")
        enabled_at: datetime | None = None
        if enabled_at_raw:
            try:
                enabled_at = datetime.fromisoformat(enabled_at_raw.replace("Z", "+00:00"))
            except ValueError as e:
                raise RuntimeError(
                    f"Unexpected autoMergeRequest.enabledAt format: {enabled_at_raw!r}"
                ) from e

        checks_rollup = pr.get("statusCheckRollup") or {}
        checks_state: str | None = checks_rollup.get("state")

        queue_entry = next((n for n in nodes if n["pullRequest"]["number"] == pr_number), None)
        return PRFetchState(
            merged=pr["merged"],
            state=pr["state"],
            mergeable=pr.get("mergeable") or "UNKNOWN",
            merge_state_status=pr.get("mergeStateStatus", "UNKNOWN"),
            auto_merge_present=auto_merge_present,
            auto_merge_enabled_at=enabled_at,
            pr_node_id=pr["id"],
            in_queue=queue_entry is not None,
            queue_state=queue_entry["state"] if queue_entry else None,
            checks_state=checks_state,
        )

    async def toggle(
        self,
        pr_number: int,
        target_branch: str,
        repo: str | None = None,
        cwd: str = ".",
    ) -> dict[str, Any]:
        """Toggle auto-merge off/on to re-enroll the PR. Never raises."""
        if not repo or "/" not in repo:
            return {
                "success": False,
                "error": f"Invalid repo format: {repo!r}. Expected 'owner/name'.",
            }
        owner, repo_name = repo.split("/", 1)
        try:
            state = await self._fetch_pr_and_queue_state(
                pr_number, owner, repo_name, target_branch
            )
            await self._toggle_auto_merge(state["pr_node_id"])
            return {"success": True, "pr_number": pr_number}
        except Exception as exc:
            logger.warning("toggle_auto_merge failed", exc_info=True)
            return {"success": False, "error": f"toggle failed: {exc}"}

    async def _enqueue_direct(self, pr_node_id: str) -> None:
        """Enqueue a PR directly via the enqueuePullRequest GraphQL mutation."""
        if not pr_node_id:
            raise ValueError("pr_node_id must be a non-empty string")
        resp = await self._ensure_client().post(
            _GRAPHQL_ENDPOINT,
            json={"query": _MUTATION_ENQUEUE_PR, "variables": {"prId": pr_node_id}},
        )
        resp.raise_for_status()
        body = resp.json()
        if "errors" in body:
            raise RuntimeError(f"GraphQL mutation error: {body['errors']}")

    async def _enable_auto_merge_direct(self, pr_node_id: str) -> None:
        """Enable auto-merge for a PR via the enablePullRequestAutoMerge mutation."""
        if not pr_node_id:
            raise ValueError("pr_node_id must be a non-empty string")
        resp = await self._ensure_client().post(
            _GRAPHQL_ENDPOINT,
            json={
                "query": _MUTATION_ENABLE_AUTO_MERGE,
                "variables": {"prId": pr_node_id, "mergeMethod": "SQUASH"},
            },
        )
        resp.raise_for_status()
        body = resp.json()
        if "errors" in body:
            raise RuntimeError(f"GraphQL mutation error: {body['errors']}")

    async def enqueue(
        self,
        pr_number: int,
        target_branch: str,
        repo: str | None = None,
        cwd: str = ".",
        auto_merge_available: bool = True,
    ) -> dict[str, Any]:
        """Enqueue a PR (via auto-merge or direct enqueue). Never raises."""
        if not repo or "/" not in repo:
            return {
                "success": False,
                "error": f"Invalid repo format: {repo!r}. Expected 'owner/name'.",
            }
        owner, repo_name = repo.split("/", 1)
        if not owner or not repo_name:
            return {
                "success": False,
                "error": f"Invalid repo format: {repo!r}. Expected 'owner/name'.",
            }
        try:
            state = await self._fetch_pr_and_queue_state(
                pr_number, owner, repo_name, target_branch
            )
            pr_node_id = state["pr_node_id"]
            if auto_merge_available:
                await self._enable_auto_merge_direct(pr_node_id)
                enrollment_method = "auto_merge"
            else:
                await self._enqueue_direct(pr_node_id)
                enrollment_method = "direct_enqueue"
            return {
                "success": True,
                "pr_number": pr_number,
                "enrollment_method": enrollment_method,
            }
        except Exception as exc:
            logger.warning("enqueue failed", exc_info=True)
            return {"success": False, "error": f"enqueue failed: {exc}"}

    async def _toggle_auto_merge(
        self, pr_node_id: str, *, auto_merge_available: bool = True
    ) -> None:
        """Re-enroll a PR: toggle disable/re-enable or enqueuePullRequest directly."""
        if not pr_node_id:
            raise ValueError("pr_node_id must be a non-empty string")
        if not auto_merge_available:
            await self._enqueue_direct(pr_node_id)
            return
        # Disable then re-enable auto-merge.
        resp = await self._ensure_client().post(
            _GRAPHQL_ENDPOINT,
            json={
                "query": _MUTATION_DISABLE_AUTO_MERGE,
                "variables": {"prId": pr_node_id},
            },
        )
        resp.raise_for_status()
        body = resp.json()
        if "errors" in body:
            raise RuntimeError(f"GraphQL mutation error: {body['errors']}")
        await asyncio.sleep(2)
        await self._enable_auto_merge_direct(pr_node_id)
