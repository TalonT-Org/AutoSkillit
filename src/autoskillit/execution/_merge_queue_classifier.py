"""PR state classifier primitives — private sub-module of execution/merge_queue.py."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import TypedDict

from autoskillit.core import PRState, get_logger

logger = get_logger(__name__)

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
    merge_group_checks_state: str | None  # None = not queried; populated on in_queue True→False


# Maps PRFetchState keys to GraphQL source paths. Validated at import time.
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
    "merge_group_checks_state": "<computed>",
}

_ALL_FETCH_STATE_KEYS = PRFetchState.__required_keys__ | PRFetchState.__optional_keys__
if set(_QUERY_FIELD_MAP) != _ALL_FETCH_STATE_KEYS:
    raise RuntimeError(
        "_QUERY_FIELD_MAP is out of sync with PRFetchState keys.\n"
        f"Missing from map: {_ALL_FETCH_STATE_KEYS - set(_QUERY_FIELD_MAP)}\n"
        f"Missing from state: {set(_QUERY_FIELD_MAP) - _ALL_FETCH_STATE_KEYS}"
    )


@dataclass(frozen=True)
class ClassificationResult:
    """Positive-signal classification outcome from _classify_pr_state."""

    terminal: PRState
    reason: str


class ClassifierInconclusive(Exception):
    """Raised when no positive gate matched — caller must continue polling."""

    def __init__(self, state: PRFetchState, reason: str) -> None:
        super().__init__(reason)
        self.state = state
        self.reason = reason


class CIStillRunning(ClassifierInconclusive):
    """CI checks legitimately in-progress — must NOT consume the inconclusive budget."""


class NoPositiveSignal(ClassifierInconclusive):
    """No positive gate matched — counts against the inconclusive budget."""


def _is_positive_stall(state: PRFetchState) -> bool:
    """True when auto-merge is enabled and merge_state_status is CLEAN or HAS_HOOKS."""
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


def _is_positive_dropped_merge_group_ci(state: PRFetchState, *, ever_enrolled: bool) -> bool:
    """True when the PR was dropped from the queue and merge-group CI is confirmed failed."""
    return _is_positive_dropped_healthy(state, ever_enrolled=ever_enrolled) and state[
        "merge_group_checks_state"
    ] in ("FAILURE", "ERROR")


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
    """Classify PR state using positive signals only.

    Raises ClassifierInconclusive when no positive gate matches.
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

    if _is_positive_dropped_merge_group_ci(state, ever_enrolled=ever_enrolled):
        return ClassificationResult(
            PRState.DROPPED_MERGE_GROUP_CI,
            "merge-group CI failed; PR ejected from queue (merge-group check run FAILURE)",
        )

    if _is_positive_dropped_healthy(state, ever_enrolled=ever_enrolled):
        return ClassificationResult(PRState.DROPPED_HEALTHY, "PR healthy but auto_merge cleared")

    if _is_not_enrolled(state, ever_enrolled=ever_enrolled):
        return ClassificationResult(
            PRState.NOT_ENROLLED, "PR was never enrolled in the merge queue"
        )

    if state["checks_state"] in {"PENDING", "EXPECTED"}:
        raise CIStillRunning(state, "checks still running")

    raise NoPositiveSignal(state, "no positive signal matched")
