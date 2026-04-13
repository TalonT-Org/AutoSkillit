"""Merge queue polling service (L1) — monitors GitHub merge queue for a PR.

Never raises. All errors are returned as structured results.
"""

from __future__ import annotations

import asyncio
import re
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, TypedDict, assert_never

import httpx

from autoskillit.core import PRState, get_logger
from autoskillit.execution.github import github_headers

_log = get_logger(__name__)


class PRFetchState(TypedDict):
    """Typed contract for _fetch_pr_and_queue_state return value."""

    merged: bool
    state: str
    mergeable: str  # "MERGEABLE" | "CONFLICTING" | "UNKNOWN"
    merge_state_status: str
    auto_merge_present: bool  # True when autoMergeRequest is not null
    auto_merge_enabled_at: datetime | None
    pr_node_id: str
    in_queue: bool
    queue_state: str | None
    checks_state: str | None  # statusCheckRollup.state; None = no checks configured


_GRAPHQL_ENDPOINT = "https://api.github.com/graphql"

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

# Maps every PRFetchState key to its GraphQL source path.
# "<computed>" means the field is derived from query results, not directly selected.
# This constant is validated at import time against PRFetchState.__required_keys__
# (mirroring the pattern in recipe/io.py:126-161).
_QUERY_FIELD_MAP: dict[str, str] = {
    "merged": "merged",
    "state": "state",
    "mergeable": "mergeable",
    "merge_state_status": "mergeStateStatus",
    "auto_merge_present": "autoMergeRequest",
    "auto_merge_enabled_at": "autoMergeRequest.enabledAt",
    "pr_node_id": "id",
    "in_queue": "<computed>",
    "queue_state": "<computed>",
    "checks_state": "statusCheckRollup.state",
}

_ALL_FETCH_STATE_KEYS = PRFetchState.__required_keys__ | PRFetchState.__optional_keys__
if set(_QUERY_FIELD_MAP) != _ALL_FETCH_STATE_KEYS:
    raise RuntimeError(
        "_QUERY_FIELD_MAP is out of sync with PRFetchState keys.\n"
        f"Missing from map: {_ALL_FETCH_STATE_KEYS - set(_QUERY_FIELD_MAP)}\n"
        f"Missing from state: {set(_QUERY_FIELD_MAP) - _ALL_FETCH_STATE_KEYS}"
    )
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


# ---------------------------------------------------------------------------
# Classifier primitives (pure functions — no I/O, no async)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ClassificationResult:
    """Positive-signal classification outcome from _classify_pr_state."""

    terminal: PRState
    reason: str


class ClassifierInconclusive(Exception):
    """Raised when no positive gate matched — caller must continue polling.

    The .state attribute exposes the full PRFetchState that was inspected,
    enabling callers to log or surface the ambiguous fields.
    """

    def __init__(self, state: PRFetchState, reason: str) -> None:
        super().__init__(reason)
        self.state = state
        self.reason = reason


def _is_positive_stall(state: PRFetchState) -> bool:
    """True when auto-merge is enabled and merge_state_status indicates the PR is
    stuck in a state where it should be in queue but is not."""
    return state["auto_merge_enabled_at"] is not None and state["merge_state_status"] in {
        "CLEAN",
        "HAS_HOOKS",
    }


def _is_positive_dropped_healthy(state: PRFetchState) -> bool:
    """True when the PR is fully healthy but auto_merge was cleared externally."""
    return (
        state["state"] == "OPEN"
        and state["mergeable"] == "MERGEABLE"
        and state["merge_state_status"] == "CLEAN"
        and state["checks_state"] in (None, "SUCCESS")
        and state["auto_merge_present"] is False
        and state["in_queue"] is False
    )


def _classify_pr_state(state: PRFetchState) -> ClassificationResult:
    """Classify PR state using positive signals only — no fall-through to EJECTED.

    Every return originates from a direct positive signal. When no positive gate
    matches, raises ClassifierInconclusive so the caller can continue polling
    within a bounded retry budget rather than silently misclassifying.

    Args:
        state: Current PR fetch state snapshot.

    Returns:
        ClassificationResult with the matched terminal PRState.

    Raises:
        ClassifierInconclusive: When no positive signal matched. .state exposes
            the full snapshot for logging/surfacing.
    """
    if state["merged"]:
        return ClassificationResult(PRState.MERGED, "PR merged")

    if state["state"] == "CLOSED":
        if state["checks_state"] in {"FAILURE", "ERROR"}:
            return ClassificationResult(PRState.EJECTED_CI_FAILURE, "PR closed after CI failure")
        return ClassificationResult(PRState.EJECTED, "PR closed while not merged")

    if state["checks_state"] in {"FAILURE", "ERROR"}:
        return ClassificationResult(PRState.EJECTED_CI_FAILURE, "checks terminal failure")

    if _is_positive_stall(state):
        return ClassificationResult(PRState.STALLED, "stall signals present")

    if state["mergeable"] == "CONFLICTING":
        return ClassificationResult(PRState.EJECTED, "conflicting changes prevent merge")

    if _is_positive_dropped_healthy(state):
        return ClassificationResult(PRState.DROPPED_HEALTHY, "PR healthy but auto_merge cleared")

    if state["checks_state"] in {"PENDING", "EXPECTED"}:
        raise ClassifierInconclusive(state, "checks still running")

    raise ClassifierInconclusive(state, "no positive signal matched")


class DefaultMergeQueueWatcher:
    """Polls GitHub merge queue state until merged, ejected, stalled, or timed out.

    Uses a single consolidated GraphQL query per poll cycle to atomically capture
    both PR state and queue entries, eliminating the race condition where two
    separate API calls straddle a merge event.

    Never raises; all errors are returned as structured dicts.
    """

    def __init__(self, token: str | None | Callable[[], str | None], max_inconclusive_retries: int = 5) -> None:
        self._token_factory: Callable[[], str | None] | None
        if callable(token):
            self._token_factory = token
            self._client: httpx.AsyncClient | None = None
        else:
            self._token_factory = None
            self._client = httpx.AsyncClient(
                headers=github_headers(token),
                limits=httpx.Limits(keepalive_expiry=60),
                timeout=30.0,
            )
        self._max_inconclusive_retries = max_inconclusive_retries

    def _ensure_client(self) -> httpx.AsyncClient:
        if self._client is None:
            resolved = self._token_factory() if self._token_factory is not None else None
            self._client = httpx.AsyncClient(
                headers=github_headers(resolved),
                limits=httpx.Limits(keepalive_expiry=60),
                timeout=30.0,
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
    ) -> dict[str, Any]:
        """Poll until PR is merged, ejected, stalled, dropped, or timeout expires.

        Args:
            pr_number: PR number to monitor.
            target_branch: Branch the merge queue targets.
            repo: "owner/name" format. Required.
            cwd: Working directory. Present to satisfy the MergeQueueWatcher protocol.
            timeout_seconds: Maximum polling duration (default 600s).
            poll_interval: Seconds between poll cycles (default 15s).
            stall_grace_period: Seconds after autoMergeRequest.enabledAt before stall
                recovery may trigger (default 60s). Prevents intervention during normal
                queue processing.
            max_stall_retries: Maximum disable/re-enable toggle attempts before
                returning pr_state="stalled" (default 3).
            not_in_queue_confirmation_cycles: Consecutive "not in queue" cycles required
                before acting on absence. Guards against race between queue exit and
                merged=true propagation (default 2).

        Returns:
            {
                "success": bool,
                "pr_state": PRState value string,
                "reason": str,
                "stall_retries_attempted": int,
            }
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
                _log.warning("fetch_pr_and_queue_state failed, retrying", exc_info=True)
                await asyncio.sleep(poll_interval)
                continue

            # In queue: reset window and continue
            if state["in_queue"]:
                not_in_queue_cycles = 0
                if state["queue_state"] == "UNMERGEABLE":
                    return _make_result(False, PRState.EJECTED, "PR is UNMERGEABLE in merge queue")
                await asyncio.sleep(poll_interval)
                continue

            not_in_queue_cycles += 1

            # Classify state using positive-signal gates
            try:
                classification = _classify_pr_state(state)
            except ClassifierInconclusive as exc:
                # Apply confirmation window before consuming inconclusive budget
                if not_in_queue_cycles < not_in_queue_confirmation_cycles:
                    await asyncio.sleep(poll_interval)
                    continue
                inconclusive_count += 1
                if inconclusive_count >= self._max_inconclusive_retries:
                    return _make_result(
                        False,
                        PRState.TIMEOUT,
                        f"Inconclusive after {self._max_inconclusive_retries} retries:"
                        f" {exc.reason}",
                    )
                await asyncio.sleep(poll_interval)
                continue

            # MERGED: definitive — bypasses confirmation window
            if classification.terminal == PRState.MERGED:
                return _make_result(True, PRState.MERGED, classification.reason)

            # CLOSED: definitive — bypasses confirmation window
            if state["state"] == "CLOSED":
                ejection_cause = (
                    "ci_failure" if classification.terminal == PRState.EJECTED_CI_FAILURE else ""
                )
                return _make_result(
                    False, classification.terminal, classification.reason, ejection_cause
                )

            # All other positive terminals require the confirmation window
            if not_in_queue_cycles < not_in_queue_confirmation_cycles:
                await asyncio.sleep(poll_interval)
                continue

            # Post-confirmation terminal dispatch
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

            else:
                # Unreachable: _classify_pr_state never returns TIMEOUT or ERROR.
                # The assert_never call provides static exhaustiveness for future
                # additions to PRState (pyright/mypy will flag any new unhandled member).
                assert_never(classification.terminal)  # type: ignore[arg-type]

        # Deadline exceeded
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
        """Disable then re-enable auto-merge for a PR to re-enroll it in the merge queue.

        Fetches the PR node ID via a single GraphQL query, then applies the
        disable/re-enable toggle. Never raises; returns a structured result dict.
        """
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
            _log.warning("toggle_auto_merge failed", exc_info=True)
            return {"success": False, "error": f"toggle failed: {exc}"}

    async def _toggle_auto_merge(self, pr_node_id: str) -> None:
        """Disable then re-enable auto-merge via GraphQL mutations."""
        if not pr_node_id:
            raise ValueError("pr_node_id must be a non-empty string")
        mutations = [
            (_MUTATION_DISABLE_AUTO_MERGE, {"prId": pr_node_id}),
            (_MUTATION_ENABLE_AUTO_MERGE, {"prId": pr_node_id, "mergeMethod": "SQUASH"}),
        ]
        for i, (mutation, variables) in enumerate(mutations):
            resp = await self._ensure_client().post(
                _GRAPHQL_ENDPOINT, json={"query": mutation, "variables": variables}
            )
            resp.raise_for_status()
            body = resp.json()
            if "errors" in body:
                raise RuntimeError(f"GraphQL mutation error: {body['errors']}")
            if i < len(mutations) - 1:
                await asyncio.sleep(2)
