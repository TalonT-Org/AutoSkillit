"""Tests for DispatchRecord schema v2 fields and backward compatibility (Group J)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from autoskillit.fleet import (
    DispatchRecord,
    DispatchStatus,
    mark_dispatch_running,
    read_state,
    write_initial_state,
)
from autoskillit.fleet.state import _SCHEMA_VERSION

pytestmark = [pytest.mark.layer("fleet"), pytest.mark.small, pytest.mark.feature("fleet")]


def _make_state(tmp_path: Path, dispatch_name: str = "a") -> Path:
    sp = tmp_path / "state.json"
    write_initial_state(
        sp, "cid-schema", "test-campaign", "/m.yaml", [DispatchRecord(name=dispatch_name)]
    )
    return sp


class TestDispatchRecordSchemaV2:
    def test_dispatch_record_has_dispatched_starttime_ticks(self) -> None:
        d = DispatchRecord(name="x")
        assert hasattr(d, "dispatched_starttime_ticks")
        assert d.dispatched_starttime_ticks == 0
        assert isinstance(d.dispatched_starttime_ticks, int)

    def test_dispatch_record_has_dispatched_boot_id(self) -> None:
        d = DispatchRecord(name="x")
        assert hasattr(d, "dispatched_boot_id")
        assert d.dispatched_boot_id == ""
        assert isinstance(d.dispatched_boot_id, str)

    def test_mark_dispatch_running_stores_starttime_ticks(self, tmp_path: Path) -> None:
        sp = _make_state(tmp_path, "a")
        mark_dispatch_running(
            sp,
            "a",
            dispatch_id="did-1",
            dispatched_pid=1234,
            starttime_ticks=42,
            boot_id="abc-boot",
        )
        state = read_state(sp)
        assert state is not None
        d = state.dispatches[0]
        assert d.status == DispatchStatus.RUNNING
        assert d.dispatched_pid == 1234
        assert d.dispatched_starttime_ticks == 42
        assert d.dispatched_boot_id == "abc-boot"

        raw = json.loads(sp.read_text())
        dispatch_raw = raw["dispatches"][0]
        assert dispatch_raw["dispatched_starttime_ticks"] == 42
        assert dispatch_raw["dispatched_boot_id"] == "abc-boot"

    def test_schema_version_is_4(self) -> None:
        assert _SCHEMA_VERSION == 4

    def test_read_state_accepts_legacy_l2_field_names(self, tmp_path: Path) -> None:
        """read_state must parse schema v3 state files that use old l2_* field names."""
        legacy_payload = {
            "schema_version": 3,
            "campaign_id": "cmp-legacy",
            "campaign_name": "legacy",
            "manifest_path": "/tmp/m.yaml",
            "started_at": 1.0,
            "dispatches": [
                {
                    "name": "d1",
                    "status": "running",
                    "dispatch_id": "did-1",
                    "l2_session_id": "sess-old",
                    "l2_session_log_dir": "/old/logs",
                    "l2_pid": 1234,
                    "l2_starttime_ticks": 5678,
                    "l2_boot_id": "boot-old",
                }
            ],
        }
        state_path = tmp_path / "state.json"
        state_path.write_text(json.dumps(legacy_payload))
        state = read_state(state_path)
        assert state is not None
        d = state.dispatches[0]
        assert d.dispatched_session_id == "sess-old"
        assert d.dispatched_session_log_dir == "/old/logs"
        assert d.dispatched_pid == 1234
        assert d.dispatched_starttime_ticks == 5678
        assert d.dispatched_boot_id == "boot-old"

    def test_dispatch_record_serializes_dispatched_field_names(self) -> None:
        """DispatchRecord.to_dict() must use dispatched_* field names."""
        d = DispatchRecord(name="x", dispatched_pid=42, dispatched_session_id="sess-new")
        raw = d.to_dict()
        assert "dispatched_pid" in raw
        assert "dispatched_session_id" in raw
        assert "l2_pid" not in raw
        assert "l2_session_id" not in raw

    def test_read_state_accepts_legacy_l3_field_names(self, tmp_path: Path) -> None:
        """read_state must parse schema v3 state files that use old l3_* field names."""
        legacy_payload = {
            "schema_version": 3,
            "campaign_id": "cmp-legacy-l3",
            "campaign_name": "legacy-l3",
            "manifest_path": "/tmp/m.yaml",
            "started_at": 1.0,
            "dispatches": [
                {
                    "name": "d1",
                    "status": "running",
                    "dispatch_id": "did-1",
                    "l3_session_id": "sess-old-l3",
                    "l3_session_log_dir": "/old/l3/logs",
                    "l3_pid": 1234,
                    "l3_starttime_ticks": 5678,
                    "l3_boot_id": "boot-old-l3",
                }
            ],
        }
        state_path = tmp_path / "state.json"
        state_path.write_text(json.dumps(legacy_payload))
        state = read_state(state_path)
        assert state is not None
        d = state.dispatches[0]
        assert d.dispatched_session_id == "sess-old-l3"
        assert d.dispatched_session_log_dir == "/old/l3/logs"
        assert d.dispatched_pid == 1234
        assert d.dispatched_starttime_ticks == 5678
        assert d.dispatched_boot_id == "boot-old-l3"
        assert d.caller_session_id == ""

    def test_read_state_handles_v1_without_ticks(self, tmp_path: Path) -> None:
        """read_state on a v1 file missing dispatched_starttime_ticks or dispatched_boot_id."""
        sp = tmp_path / "state_v1.json"
        v1_payload = {
            "schema_version": 1,
            "campaign_id": "cid-v1",
            "campaign_name": "old-campaign",
            "manifest_path": "/m.yaml",
            "started_at": 0.0,
            "dispatches": [
                {
                    "name": "dispatch-a",
                    "status": "running",
                    "dispatch_id": "did-old",
                    "l2_session_id": "",
                    "l2_session_log_dir": "",
                    "l2_pid": 9999,
                    "reason": "",
                    "token_usage": {},
                    "started_at": 0.0,
                    "ended_at": 0.0,
                }
            ],
        }
        sp.write_text(json.dumps(v1_payload))
        state = read_state(sp)
        assert state is not None
        d = state.dispatches[0]
        assert d.dispatched_pid == 9999
        assert d.dispatched_starttime_ticks == 0
        assert d.dispatched_boot_id == ""
