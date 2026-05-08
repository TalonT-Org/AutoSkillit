"""Bridge from fleet IssueSidecarEntry to core SessionCheckpoint."""

from __future__ import annotations

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
