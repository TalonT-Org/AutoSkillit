"""Cross-layer fleet concurrency tests requiring CLI layer access."""

from __future__ import annotations

import json
import threading
from pathlib import Path
from unittest.mock import patch

import pytest

from autoskillit.fleet import (
    DispatchRecord,
    resume_campaign_from_state,
    write_initial_state,
)

pytestmark = [pytest.mark.medium]

# -------------------------------------------------------------------
# Cross-caller concurrency test (requires cli layer)
# -------------------------------------------------------------------


class TestReapAndResumeMutualExclusion:
    def test_reap_and_resume_mutual_exclusion(self, tmp_path: Path) -> None:
        """Reap and resume must not corrupt state when racing on the same file.

        Sets up a state file with one RUNNING dispatch (stale PID), then
        spawns two threads: one calling _reap_stale_dispatches and one calling
        resume_campaign_from_state. Verifies the dispatch ends in exactly one
        terminal state and the state file is never corrupted.
        """
        from autoskillit.cli.fleet import _reap_stale_dispatches

        sp = tmp_path / "state.json"
        write_initial_state(sp, "cid", "camp", "/m.yaml", [DispatchRecord(name="d1")])
        raw = json.loads(sp.read_text())
        raw["dispatches"][0].update(
            {
                "status": "running",
                "dispatch_id": "did-1",
                "dispatched_pid": 99999,
                "dispatched_starttime_ticks": 0,
                "dispatched_boot_id": "boot-test",
                "started_at": 0.0,
            }
        )
        sp.write_text(json.dumps(raw))

        barrier = threading.Barrier(2, timeout=5)

        def do_reap() -> None:
            barrier.wait()
            with patch("psutil.pid_exists", return_value=False):
                with patch("autoskillit.execution.read_boot_id", return_value="boot-test"):
                    _reap_stale_dispatches(sp, dry_run=False)

        def do_resume() -> None:
            barrier.wait()
            resume_campaign_from_state(sp, continue_on_failure=True)

        t1 = threading.Thread(target=do_reap)
        t2 = threading.Thread(target=do_resume)
        t1.start()
        t2.start()
        t1.join()
        t2.join()

        state_data = json.loads(sp.read_text())
        assert len(state_data["dispatches"]) == 1
        disp = state_data["dispatches"][0]
        assert disp["status"] in ("interrupted", "resumable"), (
            f"Unexpected status {disp['status']}"
        )
        terminal_count = sum(
            1
            for d in state_data["dispatches"]
            if d["status"] in ("interrupted", "resumable", "success", "failure")
        )
        assert terminal_count == 1
