"""Tests for fleet._dispatch_reaper.reap_stale_dispatches."""

from __future__ import annotations

import json
import time
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
    dispatch_id: str = "did-reap",
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
            "dispatch_id": dispatch_id,
            "dispatched_pid": dispatched_pid,
            "dispatched_starttime_ticks": dispatched_starttime_ticks,
            "dispatched_boot_id": dispatched_boot_id,
            "dispatched_create_time": dispatched_create_time,
            "started_at": 1000.0,
        }
    )
    sp.write_text(json.dumps(raw))
    return sp


def _reap(
    state_path: Path, *, dry_run: bool = False, skip_dispatch_ids: frozenset[str] | None = None
) -> None:
    from autoskillit.fleet import reap_stale_dispatches

    reap_stale_dispatches(state_path, dry_run=dry_run, skip_dispatch_ids=skip_dispatch_ids)


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

    def test_reap_kills_orphan_via_create_time_when_ticks_zero(self, tmp_path: Path) -> None:
        """When dispatched_starttime_ticks=0, reaper falls back to create_time comparison."""
        sp = _make_running_state(
            tmp_path,
            dispatched_pid=12345,
            dispatched_starttime_ticks=0,
            dispatched_create_time=1000000.5,
        )
        with (
            patch("autoskillit.fleet._dispatch_reaper.psutil.pid_exists", return_value=True),
            patch(
                "autoskillit.fleet._dispatch_reaper.read_starttime_ticks",
                return_value=7777,  # live process has real ticks; stored dispatch has 0
            ),
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

    def test_reap_skips_dispatch_in_skip_set(self, tmp_path: Path) -> None:
        sp = _make_running_state(
            tmp_path,
            dispatch_id="test-dispatch-id",
            dispatched_pid=12345,
            dispatched_starttime_ticks=1000,
        )
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
            _reap(sp, skip_dispatch_ids=frozenset({"test-dispatch-id"}))

        mock_kill.assert_not_called()
        state = read_state(sp)
        assert state is not None
        assert state.dispatches[0].status == DispatchStatus.RUNNING

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
        assert state.dispatches[0].reason == "reaped_orphan"

    def test_reap_self_referential_pid_survives_with_skip_guard(self, tmp_path: Path) -> None:
        import os

        sp = _make_running_state(
            tmp_path,
            dispatch_id="my-dispatch",
            dispatched_pid=os.getpid(),
            dispatched_starttime_ticks=1000,
            dispatched_boot_id=BOOT_ID,
        )
        with (
            patch("autoskillit.fleet._dispatch_reaper.read_starttime_ticks", return_value=1000),
            patch("autoskillit.fleet._dispatch_reaper.read_boot_id", return_value=BOOT_ID),
        ):
            _reap(sp, skip_dispatch_ids=frozenset({"my-dispatch"}))

        state = read_state(sp)
        assert state is not None
        assert state.dispatches[0].status == DispatchStatus.RUNNING

    @pytest.mark.anyio
    async def test_reap_async_forwards_skip_dispatch_ids(self, tmp_path: Path) -> None:

        sp = tmp_path / "state.json"
        sp.write_text("{}")

        with patch("autoskillit.fleet._dispatch_reaper.reap_stale_dispatches") as mock_reap:
            from autoskillit.fleet import reap_stale_dispatches_async

            await reap_stale_dispatches_async([sp], skip_dispatch_ids=frozenset({"skip-me"}))

        mock_reap.assert_called_once_with(
            sp,
            skip_dispatch_ids=frozenset({"skip-me"}),
            own_campaign_id=None,
            min_reap_age_seconds=60.0,
            reaper_dispatch_id="",
            heartbeat_grace_seconds=90.0,
        )

    def test_reap_writes_tombstone_to_victim_session_log_dir(self, tmp_path: Path) -> None:
        """Reaper writes reaper_action.json into victim's session log dir (Test 1E)."""
        session_log_dir = tmp_path / "session-logs"
        session_log_dir.mkdir()
        sp = _make_running_state(
            tmp_path,
            dispatch_id="victim-dispatch-001",
            dispatched_pid=12345,
            dispatched_starttime_ticks=1000,
            dispatched_boot_id=BOOT_ID,
        )
        raw = json.loads(sp.read_text())
        raw["dispatches"][0]["dispatched_session_log_dir"] = str(session_log_dir)
        sp.write_text(json.dumps(raw))

        with (
            patch("autoskillit.fleet._dispatch_reaper.read_starttime_ticks", return_value=1000),
            patch("autoskillit.fleet._dispatch_reaper.read_boot_id", return_value=BOOT_ID),
            patch("autoskillit.fleet._dispatch_reaper.kill_process_tree"),
            patch("autoskillit.fleet._dispatch_reaper.psutil.pid_exists", return_value=True),
        ):
            from autoskillit.fleet import reap_stale_dispatches

            reap_stale_dispatches(sp, reaper_dispatch_id="reaper-aaa-001")

        tombstone_path = session_log_dir / "reaper_action.json"
        assert tombstone_path.exists(), "reaper_action.json should be written to session log dir"
        tombstone = json.loads(tombstone_path.read_text())
        assert tombstone["action"] == "reaped_orphan"
        assert tombstone["reaper_dispatch_id"] == "reaper-aaa-001"
        assert tombstone["victim_dispatch_id"] == "victim-dispatch-001"
        assert tombstone["victim_pid"] == 12345

    def test_reap_appends_to_central_reaper_events_log(self, tmp_path: Path) -> None:
        """Reaper appends event to reaper_events.jsonl (Test 1F)."""
        sp = _make_running_state(
            tmp_path,
            dispatch_id="victim-dispatch-002",
            dispatched_pid=12345,
            dispatched_starttime_ticks=1000,
            dispatched_boot_id=BOOT_ID,
        )

        with (
            patch("autoskillit.fleet._dispatch_reaper.read_starttime_ticks", return_value=1000),
            patch("autoskillit.fleet._dispatch_reaper.read_boot_id", return_value=BOOT_ID),
            patch("autoskillit.fleet._dispatch_reaper.kill_process_tree"),
            patch("autoskillit.fleet._dispatch_reaper.psutil.pid_exists", return_value=True),
            patch("autoskillit.fleet._dispatch_reaper.default_log_dir", return_value=tmp_path),
        ):
            from autoskillit.fleet import reap_stale_dispatches

            reap_stale_dispatches(sp, reaper_dispatch_id="reaper-bbb-002")

        log_path = tmp_path / "reaper_events.jsonl"
        assert log_path.exists(), "reaper_events.jsonl should be created"
        lines = [ln for ln in log_path.read_text().splitlines() if ln.strip()]
        assert len(lines) == 1
        event = json.loads(lines[0])
        assert event["action"] == "reaped_orphan"
        assert event["victim_dispatch_id"] == "victim-dispatch-002"
        assert event["victim_pid"] == 12345
        assert event["reaper_dispatch_id"] == "reaper-bbb-002"
        assert "ts" in event
        assert "campaign_id" in event

    def test_reaper_reason_survives_upsert_dispatch_record_by_name(self, tmp_path: Path) -> None:
        """reaper_reason/reaper_dispatch_id survive a subsequent upsert (Test 1G)."""
        from autoskillit.fleet import upsert_dispatch_record_by_name, write_initial_state

        sp = tmp_path / "state.json"
        write_initial_state(sp, "cid-1g", "1g-campaign", "/m.yaml", [DispatchRecord(name="d1")])

        raw = json.loads(sp.read_text())
        raw["dispatches"][0].update(
            {
                "status": "failure",
                "reaper_reason": "reaped_orphan",
                "reaper_dispatch_id": "abc-123",
            }
        )
        sp.write_text(json.dumps(raw))

        overwrite = DispatchRecord(
            name="d1",
            status=DispatchStatus.FAILURE,
            reason="some_other_reason",
            reaper_reason="",
            reaper_dispatch_id="",
        )
        upsert_dispatch_record_by_name(sp, overwrite)

        state = read_state(sp)
        assert state is not None
        record = state.dispatches[0]
        assert record.reaper_reason == "reaped_orphan"
        assert record.reaper_dispatch_id == "abc-123"

    def test_reap_skips_own_campaign_state_file(self, tmp_path: Path) -> None:
        sp = tmp_path / "state.json"
        write_initial_state(
            sp,
            "campaign-1",
            "campaign-one",
            "/m.yaml",
            [DispatchRecord(name="sibling")],
        )
        raw = json.loads(sp.read_text())
        raw["dispatches"][0].update(
            {
                "status": "running",
                "dispatch_id": "dispatch-b",
                "dispatched_pid": 12345,
                "dispatched_starttime_ticks": 1000,
                "dispatched_boot_id": BOOT_ID,
                "started_at": 1000.0,
            }
        )
        sp.write_text(json.dumps(raw))

        with patch("autoskillit.fleet._dispatch_reaper.kill_process_tree") as mock_kill:
            from autoskillit.fleet import reap_stale_dispatches

            reap_stale_dispatches(sp, own_campaign_id="campaign-1")

        mock_kill.assert_not_called()
        state = read_state(sp)
        assert state is not None
        assert state.dispatches[0].status == DispatchStatus.RUNNING

    def test_reap_kills_orphan_from_different_campaign(self, tmp_path: Path) -> None:
        sp = tmp_path / "state.json"
        write_initial_state(
            sp,
            "campaign-2",
            "campaign-two",
            "/m.yaml",
            [DispatchRecord(name="foreign")],
        )
        raw = json.loads(sp.read_text())
        raw["dispatches"][0].update(
            {
                "status": "running",
                "dispatch_id": "dispatch-c",
                "dispatched_pid": 12345,
                "dispatched_starttime_ticks": 1000,
                "dispatched_boot_id": BOOT_ID,
                "started_at": 1000.0,
            }
        )
        sp.write_text(json.dumps(raw))

        with (
            patch("autoskillit.fleet._dispatch_reaper.psutil.pid_exists", return_value=True),
            patch(
                "autoskillit.fleet._dispatch_reaper.read_starttime_ticks",
                return_value=1000,
            ),
            patch("autoskillit.fleet._dispatch_reaper.read_boot_id", return_value=BOOT_ID),
            patch("autoskillit.fleet._dispatch_reaper.kill_process_tree") as mock_kill,
        ):
            from autoskillit.fleet import reap_stale_dispatches

            reap_stale_dispatches(sp, own_campaign_id="campaign-1")

        mock_kill.assert_called_once_with(12345)
        state = read_state(sp)
        assert state is not None
        assert state.dispatches[0].reason == "reaped_orphan"

    def test_reap_skips_dispatch_younger_than_min_age(self, tmp_path: Path) -> None:
        sp = tmp_path / "state.json"
        write_initial_state(
            sp,
            "campaign-x",
            "campaign-x",
            "/m.yaml",
            [DispatchRecord(name="young")],
        )
        raw = json.loads(sp.read_text())
        raw["dispatches"][0].update(
            {
                "status": "running",
                "dispatch_id": "dispatch-young",
                "dispatched_pid": 12345,
                "dispatched_starttime_ticks": 1000,
                "dispatched_boot_id": BOOT_ID,
                "started_at": 1000.0,
            }
        )
        sp.write_text(json.dumps(raw))

        with (
            patch("autoskillit.fleet._dispatch_reaper.psutil.pid_exists", return_value=True),
            patch("autoskillit.fleet._dispatch_reaper.read_boot_id", return_value=BOOT_ID),
            patch("autoskillit.fleet._dispatch_reaper.kill_process_tree") as mock_kill,
            patch("autoskillit.fleet._dispatch_reaper.time.time", return_value=1005.0),
        ):
            from autoskillit.fleet import reap_stale_dispatches

            reap_stale_dispatches(sp, min_reap_age_seconds=60.0)

        mock_kill.assert_not_called()
        state = read_state(sp)
        assert state is not None
        assert state.dispatches[0].status == DispatchStatus.RUNNING

    @pytest.mark.anyio
    async def test_reap_async_skips_own_campaign_state_files(self, tmp_path: Path) -> None:
        sp1 = tmp_path / "dispatch_a.json"
        write_initial_state(
            sp1,
            "campaign-1",
            "campaign-one",
            "/m.yaml",
            [DispatchRecord(name="d-a")],
        )
        raw1 = json.loads(sp1.read_text())
        raw1["dispatches"][0].update(
            {
                "status": "running",
                "dispatch_id": "dispatch-a",
                "dispatched_pid": 11111,
                "dispatched_starttime_ticks": 100,
                "dispatched_boot_id": BOOT_ID,
                "started_at": 1000.0,
            }
        )
        sp1.write_text(json.dumps(raw1))

        sp2 = tmp_path / "dispatch_b.json"
        write_initial_state(
            sp2,
            "campaign-1",
            "campaign-one",
            "/m.yaml",
            [DispatchRecord(name="d-b")],
        )
        raw2 = json.loads(sp2.read_text())
        raw2["dispatches"][0].update(
            {
                "status": "running",
                "dispatch_id": "dispatch-b",
                "dispatched_pid": 22222,
                "dispatched_starttime_ticks": 200,
                "dispatched_boot_id": BOOT_ID,
                "started_at": 1000.0,
            }
        )
        sp2.write_text(json.dumps(raw2))

        sp3 = tmp_path / "dispatch_c.json"
        write_initial_state(
            sp3,
            "campaign-2",
            "campaign-two",
            "/m.yaml",
            [DispatchRecord(name="d-c")],
        )
        raw3 = json.loads(sp3.read_text())
        raw3["dispatches"][0].update(
            {
                "status": "running",
                "dispatch_id": "dispatch-c",
                "dispatched_pid": 33333,
                "dispatched_starttime_ticks": 300,
                "dispatched_boot_id": BOOT_ID,
                "started_at": 1000.0,
            }
        )
        sp3.write_text(json.dumps(raw3))

        with (
            patch("autoskillit.fleet._dispatch_reaper.psutil.pid_exists", return_value=True),
            patch(
                "autoskillit.fleet._dispatch_reaper.read_starttime_ticks",
                side_effect=lambda pid: {11111: 100, 22222: 200, 33333: 300}[pid],
            ),
            patch("autoskillit.fleet._dispatch_reaper.read_boot_id", return_value=BOOT_ID),
            patch("autoskillit.fleet._dispatch_reaper.kill_process_tree") as mock_kill,
        ):
            from autoskillit.fleet import reap_stale_dispatches_async

            await reap_stale_dispatches_async(
                [sp1, sp2, sp3],
                own_campaign_id="campaign-1",
                skip_dispatch_ids=frozenset({"dispatch-a"}),
            )

        killed_pids = {call.args[0] for call in mock_kill.call_args_list}
        assert 11111 not in killed_pids, "dispatch-a should be skipped (own campaign sp1)"
        assert 22222 not in killed_pids, "dispatch-b should be skipped (own campaign sp2)"
        assert 33333 in killed_pids, "dispatch-c from campaign-2 should be reaped"

    def test_reap_skips_cross_campaign_dispatch_with_fresh_heartbeat(self, tmp_path: Path) -> None:
        sp = _make_running_state(
            tmp_path,
            dispatch_id="dispatch-c",
            dispatched_pid=12345,
            dispatched_starttime_ticks=1000,
            dispatched_boot_id=BOOT_ID,
        )
        hb_path = tmp_path / "dispatch-dispatch-c.heartbeat"
        hb_path.write_text("{}")

        with (
            patch("autoskillit.fleet._dispatch_reaper.psutil.pid_exists", return_value=True),
            patch("autoskillit.fleet._dispatch_reaper.read_starttime_ticks", return_value=1000),
            patch("autoskillit.fleet._dispatch_reaper.read_boot_id", return_value=BOOT_ID),
            patch("autoskillit.fleet._dispatch_reaper.kill_process_tree") as mock_kill,
        ):
            from autoskillit.fleet import reap_stale_dispatches

            reap_stale_dispatches(sp, own_campaign_id="campaign-1", heartbeat_grace_seconds=90.0)

        mock_kill.assert_not_called()
        state = read_state(sp)
        assert state is not None
        assert state.dispatches[0].status == DispatchStatus.RUNNING

    def test_reap_kills_cross_campaign_dispatch_with_stale_heartbeat(self, tmp_path: Path) -> None:
        sp = _make_running_state(
            tmp_path,
            dispatch_id="dispatch-c",
            dispatched_pid=12345,
            dispatched_starttime_ticks=1000,
            dispatched_boot_id=BOOT_ID,
        )
        hb_path = tmp_path / "dispatch-dispatch-c.heartbeat"
        hb_path.write_text("{}")
        stale_mtime = time.time() - 200.0
        import os

        os.utime(hb_path, (stale_mtime, stale_mtime))

        with (
            patch("autoskillit.fleet._dispatch_reaper.psutil.pid_exists", return_value=True),
            patch("autoskillit.fleet._dispatch_reaper.read_starttime_ticks", return_value=1000),
            patch("autoskillit.fleet._dispatch_reaper.read_boot_id", return_value=BOOT_ID),
            patch("autoskillit.fleet._dispatch_reaper.kill_process_tree") as mock_kill,
        ):
            from autoskillit.fleet import reap_stale_dispatches

            reap_stale_dispatches(sp, own_campaign_id="campaign-1", heartbeat_grace_seconds=90.0)

        mock_kill.assert_called_once_with(12345)
        state = read_state(sp)
        assert state is not None
        assert state.dispatches[0].reason == "reaped_orphan"

    def test_reap_kills_cross_campaign_dispatch_with_no_heartbeat(self, tmp_path: Path) -> None:
        sp = _make_running_state(
            tmp_path,
            dispatch_id="dispatch-c",
            dispatched_pid=12345,
            dispatched_starttime_ticks=1000,
            dispatched_boot_id=BOOT_ID,
        )

        with (
            patch("autoskillit.fleet._dispatch_reaper.psutil.pid_exists", return_value=True),
            patch("autoskillit.fleet._dispatch_reaper.read_starttime_ticks", return_value=1000),
            patch("autoskillit.fleet._dispatch_reaper.read_boot_id", return_value=BOOT_ID),
            patch("autoskillit.fleet._dispatch_reaper.kill_process_tree") as mock_kill,
        ):
            from autoskillit.fleet import reap_stale_dispatches

            reap_stale_dispatches(sp, own_campaign_id="campaign-1", heartbeat_grace_seconds=90.0)

        mock_kill.assert_called_once_with(12345)
        state = read_state(sp)
        assert state is not None
        assert state.dispatches[0].reason == "reaped_orphan"

    def test_reap_skips_heartbeat_with_configurable_grace_period(self, tmp_path: Path) -> None:
        sp = _make_running_state(
            tmp_path,
            dispatch_id="dispatch-c",
            dispatched_pid=12345,
            dispatched_starttime_ticks=1000,
            dispatched_boot_id=BOOT_ID,
        )
        hb_path = tmp_path / "dispatch-dispatch-c.heartbeat"
        hb_path.write_text("{}")
        import os

        mtime_200s_ago = time.time() - 200.0
        os.utime(hb_path, (mtime_200s_ago, mtime_200s_ago))

        with (
            patch("autoskillit.fleet._dispatch_reaper.psutil.pid_exists", return_value=True),
            patch("autoskillit.fleet._dispatch_reaper.read_starttime_ticks", return_value=1000),
            patch("autoskillit.fleet._dispatch_reaper.read_boot_id", return_value=BOOT_ID),
            patch("autoskillit.fleet._dispatch_reaper.kill_process_tree") as mock_kill,
        ):
            from autoskillit.fleet import reap_stale_dispatches

            reap_stale_dispatches(sp, own_campaign_id="campaign-1", heartbeat_grace_seconds=300.0)

        mock_kill.assert_not_called()
        state = read_state(sp)
        assert state is not None
        assert state.dispatches[0].status == DispatchStatus.RUNNING

        sp2 = _make_running_state(
            tmp_path / "sub",
            dispatch_id="dispatch-d",
            dispatched_pid=99999,
            dispatched_starttime_ticks=2000,
            dispatched_boot_id=BOOT_ID,
        )
        hb_path2 = (tmp_path / "sub") / "dispatch-dispatch-d.heartbeat"
        hb_path2.write_text("{}")
        os.utime(hb_path2, (mtime_200s_ago, mtime_200s_ago))

        with (
            patch("autoskillit.fleet._dispatch_reaper.psutil.pid_exists", return_value=True),
            patch("autoskillit.fleet._dispatch_reaper.read_starttime_ticks", return_value=2000),
            patch("autoskillit.fleet._dispatch_reaper.read_boot_id", return_value=BOOT_ID),
            patch("autoskillit.fleet._dispatch_reaper.kill_process_tree") as mock_kill2,
        ):
            reap_stale_dispatches(sp2, own_campaign_id="campaign-1", heartbeat_grace_seconds=120.0)

        mock_kill2.assert_called_once_with(99999)
