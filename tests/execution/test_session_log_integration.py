"""Integration tests: full tracing pipeline (accumulation + flush) end-to-end."""

from __future__ import annotations

import json
import os
import sys
from datetime import UTC, datetime

import anyio
import pytest

pytestmark = pytest.mark.skipif(sys.platform != "linux", reason="Linux only")


@pytest.mark.anyio
async def test_full_tracing_pipeline_writes_distinct_timestamps(tmp_path):
    """End-to-end: snapshot accumulation + flush produces unique ts per record."""
    from autoskillit.config import LinuxTracingConfig
    from autoskillit.execution.linux_tracing import start_linux_tracing
    from autoskillit.execution.session_log import flush_session_log

    config = LinuxTracingConfig(proc_interval=0.05)
    start_ts = datetime.now(UTC).isoformat()
    async with anyio.create_task_group() as tg:
        handle = start_linux_tracing(os.getpid(), config, tg)
        await anyio.sleep(0.3)
        snaps = handle.stop()
        tg.cancel_scope.cancel()
    end_ts = datetime.now(UTC).isoformat()
    assert len(snaps) >= 2, "Need at least 2 snapshots for timestamp variance test"
    snap_dicts = [s.__dict__ for s in snaps]

    flush_session_log(
        log_dir=str(tmp_path),
        cwd="/tmp",
        session_id="integration-test-001",
        pid=os.getpid(),
        skill_command="/test",
        success=True,
        subtype="completed",
        exit_code=0,
        start_ts=start_ts,
        end_ts=end_ts,
        termination_reason="natural_exit",
        snapshot_interval_seconds=0.05,
        proc_snapshots=snap_dicts,
    )

    session_dir = tmp_path / "sessions" / "integration-test-001"
    records = [
        json.loads(line) for line in (session_dir / "proc_trace.jsonl").read_text().splitlines()
    ]
    timestamps = [r["ts"] for r in records]
    assert len(set(timestamps)) == len(timestamps), "All ts must be unique per snapshot"
    assert all(t != start_ts for t in timestamps[1:]), "ts must not be the session start time"

    summary = json.loads((session_dir / "summary.json").read_text())
    assert "end_ts" in summary
    assert "duration_seconds" in summary
    assert summary["duration_seconds"] > 0
