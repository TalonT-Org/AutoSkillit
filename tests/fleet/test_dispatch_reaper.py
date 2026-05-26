"""Tests for fleet._dispatch_reaper.reap_stale_dispatches."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import psutil
import pytest

from autoskillit.fleet import (
    DispatchRecord,
    DispatchStatus,
    read_state,
    write_initial_state,
)

pytestmark = [pytest.mark.layer("fleet"), pytest.mark.small, pytest.mark.feature("fleet")]

BOOT_ID = "boot-abc-123"
OTHER_BOOT_ID = "boot-xyz-999"


def _make_running_state(
    tmp_path: Path,
    *,
    dispatch_name: str = "d1",
    dispatched_pid: int = 12345,
    dispatched_starttime_ticks: int = 1000,
    dispatched_boot_id: str = BOOT_ID,
    dispatched_create_time: float = 0.0,
) -> Path:
    sp = tmp_path / "state.json"
    write_initial_state(
        sp, "cid-reap", "reap-campaign", "/m.yaml", [DispatchRecord(name=dispatch_name)]
    )
    raw = json.loads(sp.read_text())
    raw["dispatches"][0].update(
        {
            "status": "running",
            "dispatch_id": "did-reap",
            "dispatched_pid": dispatched_pid,
            "dispatched_starttime_ticks": dispatched_starttime_ticks,
            "dispatched_boot_id": dispatched_boot_id,
            "dispatched_create_time": dispatched_create_time,
            "started_at": 1000.0,
        }
    )
    sp.write_text(json.dumps(raw))
    return sp


def _reap(state_path: Path, *, dry_run: bool = False) -> None:
    from autoskillit.fleet import reap_stale_dispatches

    reap_stale_dispatches(state_path, dry_run=dry_run)


class TestReap:
    def test_reap_kills_orphan(self, tmp_path: Path) -> None:
        sp = _make_running_state(tmp_path, dispatched_pid=12345, dispatched_starttime_ticks=1000)
        with (
            patch("autoskillit.fleet._dispatch_reaper.psutil.pid_exists", return_value=True),
            patch(
                "autoskillit.fleet._dispatch_reaper.read_starttime_ticks",
                return_value=1000,
            ),
            patch(
                "autoskillit.fleet._dispatch_reaper.read_boot_id",
                return_value=BOOT_ID,
            ),
            patch("autoskillit.fleet._dispatch_reaper.kill_process_tree") as mock_kill,
        ):
            _reap(sp)

        mock_kill.assert_called_once_with(12345)
        state = read_state(sp)
        assert state is not None
        assert state.dispatches[0].status == DispatchStatus.INTERRUPTED
        assert state.dispatches[0].reason == "reaped_orphan"

    def test_reap_skips_recycled_pid(self, tmp_path: Path) -> None:
        sp = _make_running_state(tmp_path, dispatched_pid=12345, dispatched_starttime_ticks=1000)
        with (
            patch("autoskillit.fleet._dispatch_reaper.psutil.pid_exists", return_value=True),
            patch(
                "autoskillit.fleet._dispatch_reaper.read_starttime_ticks",
                return_value=9999,
            ),
            patch(
                "autoskillit.fleet._dispatch_reaper.read_boot_id",
                return_value=BOOT_ID,
            ),
            patch("autoskillit.fleet._dispatch_reaper.kill_process_tree") as mock_kill,
        ):
            _reap(sp)

        mock_kill.assert_not_called()
        state = read_state(sp)
        assert state is not None
        assert state.dispatches[0].status == DispatchStatus.INTERRUPTED
        assert state.dispatches[0].reason == "reaped_pid_recycled"

    def test_reap_marks_dead_pid(self, tmp_path: Path) -> None:
        sp = _make_running_state(tmp_path, dispatched_pid=12345)
        with (
            patch("autoskillit.fleet._dispatch_reaper.psutil.pid_exists", return_value=False),
            patch(
                "autoskillit.fleet._dispatch_reaper.read_boot_id",
                return_value=BOOT_ID,
            ),
            patch("autoskillit.fleet._dispatch_reaper.kill_process_tree") as mock_kill,
        ):
            _reap(sp)

        mock_kill.assert_not_called()
        state = read_state(sp)
        assert state is not None
        assert state.dispatches[0].status == DispatchStatus.INTERRUPTED
        assert state.dispatches[0].reason == "reaped_dead_pid"

    def test_reap_idempotent(self, tmp_path: Path) -> None:
        sp = _make_running_state(tmp_path, dispatched_pid=12345)
        with (
            patch("autoskillit.fleet._dispatch_reaper.psutil.pid_exists", return_value=False),
            patch(
                "autoskillit.fleet._dispatch_reaper.read_boot_id",
                return_value=BOOT_ID,
            ),
        ):
            _reap(sp)
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
        raw = json.loads(sp.read_text())
        raw["dispatches"][0]["status"] = "success"
        sp.write_text(json.dumps(raw))

        with (
            patch(
                "autoskillit.fleet._dispatch_reaper.read_boot_id",
                return_value=BOOT_ID,
            ),
            patch("autoskillit.fleet._dispatch_reaper.psutil.pid_exists") as mock_pid,
        ):
            _reap(sp)

        mock_pid.assert_not_called()
        state = read_state(sp)
        assert state is not None
        assert state.dispatches[0].status == DispatchStatus.SUCCESS

    def test_reap_skips_kill_after_reboot(self, tmp_path: Path) -> None:
        sp = _make_running_state(
            tmp_path,
            dispatched_pid=12345,
            dispatched_starttime_ticks=1000,
            dispatched_boot_id=OTHER_BOOT_ID,
        )
        with (
            patch(
                "autoskillit.fleet._dispatch_reaper.read_boot_id",
                return_value=BOOT_ID,
            ),
            patch("autoskillit.fleet._dispatch_reaper.psutil.pid_exists", return_value=True),
            patch("autoskillit.fleet._dispatch_reaper.kill_process_tree") as mock_kill,
        ):
            _reap(sp)

        mock_kill.assert_not_called()
        state = read_state(sp)
        assert state is not None
        assert state.dispatches[0].reason == "reaped_pid_recycled"

    def test_reap_dry_run_does_not_modify_state(self, tmp_path: Path) -> None:
        sp = _make_running_state(tmp_path, dispatched_pid=12345, dispatched_starttime_ticks=1000)
        original_text = sp.read_text()

        with (
            patch("autoskillit.fleet._dispatch_reaper.psutil.pid_exists", return_value=True),
            patch(
                "autoskillit.fleet._dispatch_reaper.read_starttime_ticks",
                return_value=1000,
            ),
            patch(
                "autoskillit.fleet._dispatch_reaper.read_boot_id",
                return_value=BOOT_ID,
            ),
            patch("autoskillit.fleet._dispatch_reaper.kill_process_tree"),
        ):
            _reap(sp, dry_run=True)

        assert sp.read_text() == original_text

    def test_reap_sequential_idempotency(self, tmp_path: Path) -> None:
        sp = _make_running_state(tmp_path, dispatched_pid=12345)

        with (
            patch("autoskillit.fleet._dispatch_reaper.psutil.pid_exists", return_value=False),
            patch(
                "autoskillit.fleet._dispatch_reaper.read_boot_id",
                return_value=BOOT_ID,
            ),
        ):
            _reap(sp)
            _reap(sp)

        state = read_state(sp)
        assert state is not None
        assert state.dispatches[0].status == DispatchStatus.INTERRUPTED

    def test_reap_kills_orphan_via_create_time_fallback(self, tmp_path: Path) -> None:
        sp = _make_running_state(
            tmp_path,
            dispatched_pid=12345,
            dispatched_starttime_ticks=1000,
            dispatched_create_time=1000000.5,
        )
        with (
            patch("autoskillit.fleet._dispatch_reaper.psutil.pid_exists", return_value=True),
            patch("autoskillit.fleet._dispatch_reaper.read_starttime_ticks", return_value=None),
            patch("autoskillit.fleet._dispatch_reaper.read_boot_id", return_value=BOOT_ID),
            patch("autoskillit.fleet._dispatch_reaper.kill_process_tree") as mock_kill,
            patch("autoskillit.fleet._dispatch_reaper.psutil.Process") as mock_proc_cls,
        ):
            mock_proc_cls.return_value.create_time.return_value = 1000000.5
            _reap(sp)

        mock_kill.assert_called_once_with(12345)
        state = read_state(sp)
        assert state is not None
        assert state.dispatches[0].reason == "reaped_orphan"

    def test_reap_skips_recycled_pid_via_create_time(self, tmp_path: Path) -> None:
        sp = _make_running_state(
            tmp_path,
            dispatched_pid=12345,
            dispatched_starttime_ticks=1000,
            dispatched_create_time=1000000.5,
        )
        with (
            patch("autoskillit.fleet._dispatch_reaper.psutil.pid_exists", return_value=True),
            patch("autoskillit.fleet._dispatch_reaper.read_starttime_ticks", return_value=None),
            patch("autoskillit.fleet._dispatch_reaper.read_boot_id", return_value=BOOT_ID),
            patch("autoskillit.fleet._dispatch_reaper.kill_process_tree") as mock_kill,
            patch("autoskillit.fleet._dispatch_reaper.psutil.Process") as mock_proc_cls,
        ):
            mock_proc_cls.return_value.create_time.return_value = 9999999.0
            _reap(sp)

        mock_kill.assert_not_called()
        state = read_state(sp)
        assert state is not None
        assert state.dispatches[0].reason == "reaped_pid_recycled"

    def test_reap_no_create_time_marks_recycled(self, tmp_path: Path) -> None:
        sp = _make_running_state(
            tmp_path,
            dispatched_pid=12345,
            dispatched_starttime_ticks=0,
            dispatched_create_time=0.0,
        )
        with (
            patch("autoskillit.fleet._dispatch_reaper.psutil.pid_exists", return_value=True),
            patch("autoskillit.fleet._dispatch_reaper.read_starttime_ticks", return_value=None),
            patch("autoskillit.fleet._dispatch_reaper.read_boot_id", return_value=BOOT_ID),
            patch("autoskillit.fleet._dispatch_reaper.kill_process_tree") as mock_kill,
        ):
            _reap(sp)

        mock_kill.assert_not_called()
        state = read_state(sp)
        assert state is not None
        assert state.dispatches[0].reason == "reaped_pid_recycled"

    def test_reap_create_time_nosuchprocess_marks_dead(self, tmp_path: Path) -> None:
        sp = _make_running_state(
            tmp_path,
            dispatched_pid=12345,
            dispatched_starttime_ticks=1000,
            dispatched_create_time=1000000.5,
        )
        with (
            patch("autoskillit.fleet._dispatch_reaper.psutil.pid_exists", return_value=True),
            patch("autoskillit.fleet._dispatch_reaper.read_starttime_ticks", return_value=None),
            patch("autoskillit.fleet._dispatch_reaper.read_boot_id", return_value=BOOT_ID),
            patch("autoskillit.fleet._dispatch_reaper.kill_process_tree") as mock_kill,
            patch("autoskillit.fleet._dispatch_reaper.psutil.Process") as mock_proc_cls,
        ):
            mock_proc_cls.return_value.create_time.side_effect = psutil.NoSuchProcess(12345)
            _reap(sp)

        mock_kill.assert_not_called()
        state = read_state(sp)
        assert state is not None
        assert state.dispatches[0].reason == "reaped_dead_pid"
