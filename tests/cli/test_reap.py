"""Tests for _reap_stale_dispatches in cli/_fleet.py (Group J)."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from autoskillit.fleet import (
    DispatchRecord,
    DispatchStatus,
    read_state,
    write_initial_state,
)

pytestmark = [pytest.mark.layer("cli"), pytest.mark.small, pytest.mark.feature("fleet")]

BOOT_ID = "boot-abc-123"
OTHER_BOOT_ID = "boot-xyz-999"


def _make_running_state(
    tmp_path: Path,
    *,
    dispatch_name: str = "d1",
    l2_pid: int = 12345,
    l2_starttime_ticks: int = 1000,
    l2_boot_id: str = BOOT_ID,
) -> Path:
    """Create a state file with a single RUNNING dispatch."""
    sp = tmp_path / "state.json"
    write_initial_state(
        sp, "cid-reap", "reap-campaign", "/m.yaml", [DispatchRecord(name=dispatch_name)]
    )
    # Inject RUNNING state directly into the JSON to bypass transition validation
    raw = json.loads(sp.read_text())
    raw["dispatches"][0].update(
        {
            "status": "running",
            "dispatch_id": "did-reap",
            "l2_pid": l2_pid,
            "l2_starttime_ticks": l2_starttime_ticks,
            "l2_boot_id": l2_boot_id,
            "started_at": 1000.0,
        }
    )
    sp.write_text(json.dumps(raw))
    return sp


def _reap(state_path: Path, *, dry_run: bool = False) -> None:
    from autoskillit.cli.fleet import _reap_stale_dispatches

    _reap_stale_dispatches(state_path, dry_run=dry_run)


class TestReap:
    def test_reap_kills_orphan(self, tmp_path: Path) -> None:
        sp = _make_running_state(tmp_path, l2_pid=12345, l2_starttime_ticks=1000)
        with (
            patch("psutil.pid_exists", return_value=True),
            patch(
                "autoskillit.execution.read_starttime_ticks",
                return_value=1000,
            ),
            patch(
                "autoskillit.execution.read_boot_id",
                return_value=BOOT_ID,
            ),
            patch("autoskillit.execution.kill_process_tree") as mock_kill,
        ):
            _reap(sp)

        mock_kill.assert_called_once_with(12345)
        state = read_state(sp)
        assert state is not None
        assert state.dispatches[0].status == DispatchStatus.INTERRUPTED
        assert state.dispatches[0].reason == "reaped_orphan"

    def test_reap_skips_recycled_pid(self, tmp_path: Path) -> None:
        sp = _make_running_state(tmp_path, l2_pid=12345, l2_starttime_ticks=1000)
        with (
            patch("psutil.pid_exists", return_value=True),
            patch(
                "autoskillit.execution.read_starttime_ticks",
                return_value=9999,  # different ticks → recycled
            ),
            patch(
                "autoskillit.execution.read_boot_id",
                return_value=BOOT_ID,
            ),
            patch("autoskillit.execution.kill_process_tree") as mock_kill,
        ):
            _reap(sp)

        mock_kill.assert_not_called()
        state = read_state(sp)
        assert state is not None
        assert state.dispatches[0].status == DispatchStatus.INTERRUPTED
        assert state.dispatches[0].reason == "reaped_pid_recycled"

    def test_reap_marks_dead_pid(self, tmp_path: Path) -> None:
        sp = _make_running_state(tmp_path, l2_pid=12345)
        with (
            patch("psutil.pid_exists", return_value=False),
            patch(
                "autoskillit.execution.read_boot_id",
                return_value=BOOT_ID,
            ),
            patch("autoskillit.execution.kill_process_tree") as mock_kill,
        ):
            _reap(sp)

        mock_kill.assert_not_called()
        state = read_state(sp)
        assert state is not None
        assert state.dispatches[0].status == DispatchStatus.INTERRUPTED
        assert state.dispatches[0].reason == "reaped_dead_pid"

    def test_reap_idempotent(self, tmp_path: Path) -> None:
        sp = _make_running_state(tmp_path, l2_pid=12345)
        with (
            patch("psutil.pid_exists", return_value=False),
            patch(
                "autoskillit.execution.read_boot_id",
                return_value=BOOT_ID,
            ),
        ):
            _reap(sp)
            # Second invocation: dispatch is now INTERRUPTED → no-op
            _reap(sp)

        state = read_state(sp)
        assert state is not None
        assert state.dispatches[0].status == DispatchStatus.INTERRUPTED

    def test_reap_no_running_dispatches(self, tmp_path: Path) -> None:
        sp = tmp_path / "state.json"
        write_initial_state(
            sp,
            "cid-done",
            "done-campaign",
            "/m.yaml",
            [DispatchRecord(name="d1")],
        )
        # Mark d1 as SUCCESS directly in JSON
        raw = json.loads(sp.read_text())
        raw["dispatches"][0]["status"] = "success"
        sp.write_text(json.dumps(raw))

        with (
            patch(
                "autoskillit.execution.read_boot_id",
                return_value=BOOT_ID,
            ),
            patch("psutil.pid_exists") as mock_pid,
        ):
            _reap(sp)

        mock_pid.assert_not_called()
        state = read_state(sp)
        assert state is not None
        assert state.dispatches[0].status == DispatchStatus.SUCCESS

    def test_reap_skips_kill_after_reboot(self, tmp_path: Path) -> None:
        """Dispatch with different l2_boot_id → machine rebooted, no kill."""
        sp = _make_running_state(
            tmp_path,
            l2_pid=12345,
            l2_starttime_ticks=1000,
            l2_boot_id=OTHER_BOOT_ID,
        )
        with (
            patch(
                "autoskillit.execution.read_boot_id",
                return_value=BOOT_ID,  # current boot differs from stored
            ),
            patch("psutil.pid_exists", return_value=True),
            patch("autoskillit.execution.kill_process_tree") as mock_kill,
        ):
            _reap(sp)

        mock_kill.assert_not_called()
        state = read_state(sp)
        assert state is not None
        assert state.dispatches[0].reason == "reaped_pid_recycled"

    def test_reap_dry_run_does_not_modify_state(self, tmp_path: Path) -> None:
        sp = _make_running_state(tmp_path, l2_pid=12345, l2_starttime_ticks=1000)
        original_text = sp.read_text()

        with (
            patch("psutil.pid_exists", return_value=True),
            patch(
                "autoskillit.execution.read_starttime_ticks",
                return_value=1000,
            ),
            patch(
                "autoskillit.execution.read_boot_id",
                return_value=BOOT_ID,
            ),
            patch("autoskillit.execution.kill_process_tree"),
        ):
            _reap(sp, dry_run=True)

        assert sp.read_text() == original_text

    def test_reap_concurrent_flock(self, tmp_path: Path) -> None:
        """Two concurrent reap calls — second sees terminal states and skips cleanly."""
        sp = _make_running_state(tmp_path, l2_pid=12345)

        with (
            patch("psutil.pid_exists", return_value=False),
            patch(
                "autoskillit.execution.read_boot_id",
                return_value=BOOT_ID,
            ),
        ):
            _reap(sp)
            # Second call: dispatch is already INTERRUPTED; _reap must not raise
            _reap(sp)

        state = read_state(sp)
        assert state is not None
        assert state.dispatches[0].status == DispatchStatus.INTERRUPTED
