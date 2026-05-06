"""Merge-queue test helper factories — used by test_merge_queue_ejection and test_merge_queue_polling."""

from __future__ import annotations

from datetime import datetime

from autoskillit.execution.merge_queue import DefaultMergeQueueWatcher, PRFetchState


def _make_watcher() -> DefaultMergeQueueWatcher:
    return DefaultMergeQueueWatcher(token=None)


def _queue_state(
    *,
    merged: bool = False,
    state: str = "OPEN",
    mergeable: str = "MERGEABLE",
    merge_state_status: str = "CLEAN",
    auto_merge_present: bool = False,
    auto_merge_enabled_at: datetime | None = None,
    pr_node_id: str = "PR_kwDO_test",
    in_queue: bool = False,
    queue_state: str | None = None,
    checks_state: str | None = None,
    merge_group_checks_state: str | None = None,
) -> PRFetchState:
    return {
        "merged": merged,
        "state": state,
        "mergeable": mergeable,
        "merge_state_status": merge_state_status,
        "auto_merge_present": auto_merge_present,
        "auto_merge_enabled_at": auto_merge_enabled_at,
        "pr_node_id": pr_node_id,
        "in_queue": in_queue,
        "queue_state": queue_state,
        "checks_state": checks_state,
        "merge_group_checks_state": merge_group_checks_state,
    }
