"""Shared state setup for dispatch reaper tests."""

from __future__ import annotations

import json
from pathlib import Path

from autoskillit.fleet import DispatchRecord, write_initial_state

BOOT_ID = "boot-abc-123"


def _make_running_state(
    tmp_path: Path,
    *,
    dispatch_name: str = "d1",
    dispatch_id: str = "did-reap",
    dispatched_pid: int = 12345,
    dispatched_starttime_ticks: int = 1000,
    dispatched_boot_id: str = BOOT_ID,
    dispatched_create_time: float = 0.0,
) -> Path:
    state_path = tmp_path / "state.json"
    write_initial_state(
        state_path,
        "cid-reap",
        "reap-campaign",
        "/m.yaml",
        [DispatchRecord(name=dispatch_name)],
    )
    raw = json.loads(state_path.read_text())
    raw["dispatches"][0].update(
        {
            "status": "running",
            "dispatch_id": dispatch_id,
            "dispatched_pid": dispatched_pid,
            "dispatched_starttime_ticks": dispatched_starttime_ticks,
            "dispatched_boot_id": dispatched_boot_id,
            "dispatched_create_time": dispatched_create_time,
            "started_at": 1000.0,
        }
    )
    state_path.write_text(json.dumps(raw))
    return state_path
