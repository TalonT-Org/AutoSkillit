"""Bridge from fleet progress sources to core SessionCheckpoint.

Two progress sources feed into SessionCheckpoint:
- Sidecar (issue-level): completed issue URLs from IssueSidecarEntry
- Pipeline tracker (step-level): completed recipe steps from tracker JSON
"""

from __future__ import annotations

from typing import Any

from autoskillit.core import SessionCheckpoint
from autoskillit.fleet.sidecar import IssueSidecarEntry


def checkpoint_from_sidecar(entries: list[IssueSidecarEntry]) -> SessionCheckpoint:
    completed = [e.issue_url for e in entries if e.status == "completed"]
    ts = entries[-1].ts if entries else ""
    return SessionCheckpoint(
        completed_items=completed,
        step_name="fleet_dispatch",
        progress_pct=0.0,
        ts=ts,
    )


def checkpoint_from_tracker(tracker_data: dict[str, Any] | None) -> SessionCheckpoint | None:
    if tracker_data is None:
        return None
    steps = tracker_data.get("steps", {})
    if not isinstance(steps, dict):
        return None
    completed = [
        name
        for name, info in steps.items()
        if isinstance(info, dict) and info.get("status") == "complete"
    ]
    if not completed:
        return None
    completed_with_ts = [(name, steps[name].get("completed_at", "")) for name in completed]
    completed_with_ts.sort(key=lambda x: x[1])
    last_step_name = completed_with_ts[-1][0]
    last_ts = completed_with_ts[-1][1]
    return SessionCheckpoint(
        completed_items=[name for name, _ in completed_with_ts],
        step_name=last_step_name,
        progress_pct=len(completed) / len(steps),
        ts=last_ts,
    )
