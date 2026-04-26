"""Merge queue polling service (L1) — monitors GitHub merge queue for a PR.

Never raises. All errors are returned as structured results.
"""

from __future__ import annotations

import asyncio
import fnmatch
import random
import re
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal, TypedDict, assert_never

import httpx

from autoskillit.core import PRState, YAMLError, get_logger, load_yaml
from autoskillit.execution.github import github_headers

_log = get_logger(__name__)

# All GitHub merge_state_status values known to be returned by the GraphQL API.
# https://docs.github.com/en/graphql/reference/enums#mergestatestatus
KNOWN_MQ_MERGE_STATE_STATUSES: frozenset[str] = frozenset(
    {
        "BEHIND",
        "BLOCKED",
        "CLEAN",
        "DIRTY",
        "HAS_HOOKS",
        "UNKNOWN",
        "UNSTABLE",
    }
)
assert "CLEAN" in KNOWN_MQ_MERGE_STATE_STATUSES  # Import-time drift guard


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

_MUTATION_ENQUEUE_PR = """
mutation EnqueuePullRequest($prId: ID!) {
  enqueuePullRequest(input: {pullRequestId: $prId}) {
    mergeQueueEntry { id }
  }
}
"""

# Repo-level state query: consolidates three former run_cmd steps into one HTTP round-trip.
# Distinct from _QUERY (PR-level + queue-entries); do not merge these constants.
# Returns: mergeQueue presence, autoMergeAllowed flag, and workflow file texts for
# merge_group trigger detection — all in a single GraphQL call.
_REPO_STATE_QUERY = """
query GetRepoMergeState($owner: String!, $repo: String!, $branch: String!) {
  repository(owner: $owner, name: $repo) {
    mergeQueue(branch: $branch) {
      id
    }
    autoMergeAllowed
    object(expression: "HEAD:.github/workflows") {
      ... on Tree {
        entries {
          name
          object {
            ... on Blob {
              text
            }
          }
        }
      }
    }
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


class CIStillRunning(ClassifierInconclusive):
    """CI checks are legitimately in-progress (PENDING or EXPECTED).

    Expected transient state — outer timeout_seconds bounds the wait.
    Must NOT consume the inconclusive budget.
    """


class NoPositiveSignal(ClassifierInconclusive):
    """No positive classifier gate matched — state is genuinely ambiguous.

    Bounded by max_inconclusive_retries. Counts against the inconclusive budget.
    """


def _is_positive_stall(state: PRFetchState) -> bool:
    """True when auto-merge is enabled and merge_state_status indicates the PR is
    stuck in a state where it should be in queue but is not."""
    return state["auto_merge_enabled_at"] is not None and state["merge_state_status"] in {
        "CLEAN",
        "HAS_HOOKS",
    }


def _is_positive_dropped_healthy(state: PRFetchState, *, ever_enrolled: bool) -> bool:
    """True when the PR is fully healthy but auto_merge was cleared externally."""
    if not ever_enrolled:
        return False
    return (
        state["state"] == "OPEN"
        and state["mergeable"] == "MERGEABLE"
        and state["merge_state_status"] == "CLEAN"
        and state["checks_state"] in (None, "SUCCESS")
        and state["auto_merge_present"] is False
        and state["in_queue"] is False
    )


def _is_not_enrolled(state: PRFetchState, *, ever_enrolled: bool) -> bool:
    """True when the PR is healthy but was never enrolled in the merge queue."""
    if ever_enrolled:
        return False
    return (
        state["state"] == "OPEN"
        and state["mergeable"] == "MERGEABLE"
        and state["merge_state_status"] == "CLEAN"
        and state["checks_state"] in (None, "SUCCESS")
        and state["auto_merge_present"] is False
        and state["in_queue"] is False
    )


def _classify_pr_state(state: PRFetchState, *, ever_enrolled: bool) -> ClassificationResult:
    """Classify PR state using positive signals only — no fall-through to EJECTED.

    Every return originates from a direct positive signal. When no positive gate
    matches, raises ClassifierInconclusive so the caller can continue polling
    within a bounded retry budget rather than silently misclassifying.

    Args:
        state: Current PR fetch state snapshot.
        ever_enrolled: Whether the PR was ever observed in the queue or with auto-merge.

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

    if _is_positive_dropped_healthy(state, ever_enrolled=ever_enrolled):
        return ClassificationResult(PRState.DROPPED_HEALTHY, "PR healthy but auto_merge cleared")

    if _is_not_enrolled(state, ever_enrolled=ever_enrolled):
        return ClassificationResult(
            PRState.NOT_ENROLLED, "PR was never enrolled in the merge queue"
        )

    if state["checks_state"] in {"PENDING", "EXPECTED"}:
        raise CIStillRunning(state, "checks still running")

    raise NoPositiveSignal(state, "no positive signal matched")


class DefaultMergeQueueWatcher:
    """Polls GitHub merge queue state until merged, ejected, stalled, or timed out.

    Uses a single consolidated GraphQL query per poll cycle to atomically capture
    both PR state and queue entries, eliminating the race condition where two
    separate API calls straddle a merge event.

    Never raises; all errors are returned as structured dicts.
    """

    def __init__(
        self,
        token: str | None | Callable[[], str | None],
    ) -> None:
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
        max_inconclusive_retries: int = 5,
        auto_merge_available: bool = True,
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
            max_inconclusive_retries: Maximum NoPositiveSignal cycles (beyond the
                confirmation window) before returning pr_state="timeout" (default 5).
            auto_merge_available: Whether the repository allows auto-merge. When False,
                stall recovery uses enqueuePullRequest instead of toggle.

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
                _log.warning("fetch_pr_and_queue_state failed, retrying", exc_info=True)
                await asyncio.sleep(poll_interval)
                continue

            if state["in_queue"] or state["auto_merge_present"]:
                ever_enrolled = True

            # In queue: reset window and continue
            if state["in_queue"]:
                not_in_queue_cycles = 0
                inconclusive_count = 0
                if state["queue_state"] == "UNMERGEABLE":
                    return _make_result(False, PRState.EJECTED, "PR is UNMERGEABLE in merge queue")
                await asyncio.sleep(poll_interval)
                continue

            not_in_queue_cycles += 1

            # Classify state using positive-signal gates
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
                        await self._toggle_auto_merge(
                            state["pr_node_id"],
                            auto_merge_available=auto_merge_available,
                        )
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

            elif classification.terminal == PRState.NOT_ENROLLED:
                return _make_result(False, PRState.NOT_ENROLLED, classification.reason)

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
        """Enqueue a PR using the correct enrollment strategy.

        When auto_merge_available is True, uses enablePullRequestAutoMerge.
        When False, uses the enqueuePullRequest GraphQL mutation directly.
        Never raises; returns a structured result dict.
        """
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
            _log.warning("enqueue failed", exc_info=True)
            return {"success": False, "error": f"enqueue failed: {exc}"}

    async def _toggle_auto_merge(
        self, pr_node_id: str, *, auto_merge_available: bool = True
    ) -> None:
        """Re-enroll a PR in the merge queue.

        When auto_merge_available is True, uses the disable/re-enable toggle.
        When False, uses enqueuePullRequest directly (skip toggle entirely).
        """
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


# ---------------------------------------------------------------------------
# Repo-level state helper (used by check_repo_merge_state MCP tool)
# ---------------------------------------------------------------------------


def _text_has_push_trigger(text: str) -> bool:
    """Return True if a workflow file text declares a push trigger.

    Checks for the three YAML forms GitHub supports for a push trigger:
    - ``on: [push, ...]``  (list form)
    - ``on: push``         (scalar form)
    - ``push:``            (mapping form under ``on:``)

    Note: the first two patterns (``on: [push``, ``on: push``) are reliably
    scoped to YAML trigger syntax. The third (``push:``) may match inside
    comments or run-step values — acceptable for the positive-signal-only
    classification heuristic but not precise.
    """
    return any(pat in text for pat in ("on: [push", "on: push", "push:"))


def _push_trigger_applies_to_branch(text: str, branch: str) -> bool:
    """Return True if the workflow push trigger fires for the given branch.

    Parses the YAML to inspect push.branches / push.branches-ignore filters.
    Falls back to presence-only heuristic on YAML parse failure (safe for
    ambiguous/binary blobs). Supports GitHub's fnmatch-compatible glob patterns
    (e.g. 'feature/**', 'release-*').
    """
    try:
        parsed = load_yaml(text)
    except YAMLError:
        return _text_has_push_trigger(text)

    if not isinstance(parsed, dict):
        return False

    # PyYAML (YAML 1.1) parses the bare key `on` as boolean True.
    # Accept both to be safe.
    on_value = parsed.get(True, parsed.get("on"))
    if on_value == "push":
        return True
    if isinstance(on_value, list):
        return "push" in on_value
    if not isinstance(on_value, dict) or "push" not in on_value:
        return False

    push_cfg = on_value["push"]
    if not isinstance(push_cfg, dict) or not push_cfg:
        # push: null or push: {} — no branch filter, fires for all branches
        return True

    branches = push_cfg.get("branches")
    branches_ignore = push_cfg.get("branches-ignore")

    if branches is not None:
        return any(fnmatch.fnmatch(branch, pat) for pat in branches)
    if branches_ignore is not None:
        return not any(fnmatch.fnmatch(branch, pat) for pat in branches_ignore)
    return True


def _has_merge_group_trigger(text: str) -> bool:
    """Return True if the workflow declares a merge_group trigger.

    Parses YAML to inspect the on: key rather than relying on substring
    matching, which can false-positive on comments or shell strings.
    Falls back to substring heuristic on YAML parse failure.
    """
    try:
        parsed = load_yaml(text)
    except YAMLError:
        return "merge_group" in text
    if not isinstance(parsed, dict):
        return False
    on_value = parsed.get(True, parsed.get("on"))
    if on_value == "merge_group":
        return True
    if isinstance(on_value, list):
        return "merge_group" in on_value
    if isinstance(on_value, dict):
        return "merge_group" in on_value
    return False


_RATE_LIMIT_MAX_ATTEMPTS = 3
_RATE_LIMIT_SECONDARY_MARKER = "secondary rate limit"


def _is_secondary_rate_limit(resp: httpx.Response) -> bool:
    """Return True when a 403 response is a GitHub secondary rate limit.

    GitHub returns HTTP 403 (not 429) for secondary rate limits.
    The response body contains the phrase "secondary rate limit".
    Primary rate limits use HTTP 429 or include x-ratelimit-remaining: 0.
    """
    if resp.status_code != 403:
        return False
    try:
        text = resp.text.lower()
    except Exception:
        _log.warning("Failed to read response body for rate-limit check", exc_info=True)
        return False
    return _RATE_LIMIT_SECONDARY_MARKER in text


def _retry_after_seconds(attempt: int, resp: httpx.Response) -> float:
    """Return seconds to sleep before the next retry attempt.

    Prefers the Retry-After header (integer seconds) when present and valid.
    Falls back to full-jitter exponential backoff: random(0, min(60, 1 * 2^attempt)).
    """
    try:
        header_val = resp.headers.get("Retry-After", "")
        if header_val:
            return float(header_val)
    except (ValueError, AttributeError):
        pass
    return random.uniform(0, min(60.0, 1.0 * (2**attempt)))


async def fetch_repo_merge_state(
    owner: str,
    repo: str,
    branch: str,
    token: str | None,
) -> dict[str, bool | str | None]:
    """Fetch repository merge-state in a single GraphQL round-trip.

    Returns a dict with four keys:
    - ``queue_available``: the branch has an active GitHub merge queue (bool).
    - ``merge_group_trigger``: at least one CI workflow declares the
      ``merge_group`` event trigger (bool).
    - ``auto_merge_available``: the repository has auto-merge enabled (bool).
    - ``ci_event``: ``"push"`` when any workflow declares a push trigger
      that fires for the given branch, or ``None`` otherwise (match-any —
      ci.py scope.event=None lets head_sha provide correctness).

    Null-handling:
    - ``mergeQueue is null`` → ``queue_available: False``  (no queue)
    - ``object is null`` → ``merge_group_trigger: False``, ``ci_event: None``  (no workflows dir)
    - ``entry.object.text is null`` → skip entry (binary/large file)
    - GraphQL ``autoMergeAllowed`` field error (GHES 3.0.x) → ``auto_merge_available: False``

    Only transport-level failures (network timeout, non-200 HTTP status) are
    allowed to propagate; callers are expected to handle them.

    Historical note: Issue #498 ("Merge queue detection should validate workflow has
    merge_group trigger") established the merge_group_trigger field. The ci_event
    field is a closely related extension — verify that the push-trigger scan does
    not regress the merge_group-only detection that #498 established.
    """
    resp: httpx.Response | None = None
    for attempt in range(_RATE_LIMIT_MAX_ATTEMPTS):
        async with httpx.AsyncClient(
            headers=github_headers(token),
            timeout=30.0,
        ) as client:
            resp = await client.post(
                _GRAPHQL_ENDPOINT,
                json={
                    "query": _REPO_STATE_QUERY,
                    "variables": {"owner": owner, "repo": repo, "branch": branch},
                },
            )
        if resp.status_code == 429 or _is_secondary_rate_limit(resp):
            sleep_secs = _retry_after_seconds(attempt, resp)
            _log.warning(
                "fetch_repo_merge_state rate limited",
                status=resp.status_code,
                attempt=attempt,
                sleep_secs=sleep_secs,
            )
            await asyncio.sleep(sleep_secs)
            continue
        resp.raise_for_status()
        break
    else:
        assert resp is not None, "_RATE_LIMIT_MAX_ATTEMPTS must be >= 1"
        resp.raise_for_status()

    assert resp is not None
    body = resp.json()

    # GitHub GraphQL always returns a JSON object; guard against unexpected shapes.
    if not isinstance(body, dict):
        body = {}

    # Gracefully handle GHES 3.0.x where autoMergeAllowed doesn't exist.
    auto_merge_field_missing = any(
        "autoMergeAllowed" in str(e.get("message", "")) for e in body.get("errors", [])
    )

    repo_data: dict[str, Any] = (body.get("data") or {}).get("repository") or {}
    queue_available = repo_data.get("mergeQueue") is not None
    auto_merge_available = (
        False if auto_merge_field_missing else bool(repo_data.get("autoMergeAllowed", False))
    )

    # Scan workflow files for push and merge_group trigger declarations.
    # Both flags are derived from the same Blob.text scan — no extra round-trips.
    merge_group_trigger = False
    has_push_trigger = False
    workflows_tree = repo_data.get("object")
    if workflows_tree is not None:
        for entry in workflows_tree.get("entries", []):
            blob = entry.get("object") or {}
            text = blob.get("text")
            if text is None:
                continue  # binary or oversized blob — skip
            if _has_merge_group_trigger(text):
                merge_group_trigger = True
            if _push_trigger_applies_to_branch(text, branch):
                has_push_trigger = True

    ci_event: Literal["push"] | None = "push" if has_push_trigger else None

    return {
        "queue_available": queue_available,
        "merge_group_trigger": merge_group_trigger,
        "auto_merge_available": auto_merge_available,
        "ci_event": ci_event,
    }
