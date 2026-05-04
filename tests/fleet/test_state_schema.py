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
    def test_dispatch_record_has_l3_starttime_ticks(self) -> None:
        d = DispatchRecord(name="x")
        assert hasattr(d, "l3_starttime_ticks")
        assert d.l3_starttime_ticks == 0
        assert isinstance(d.l3_starttime_ticks, int)

    def test_dispatch_record_has_l3_boot_id(self) -> None:
        d = DispatchRecord(name="x")
        assert hasattr(d, "l3_boot_id")
        assert d.l3_boot_id == ""
        assert isinstance(d.l3_boot_id, str)

    def test_mark_dispatch_running_stores_starttime_ticks(self, tmp_path: Path) -> None:
        sp = _make_state(tmp_path, "a")
        mark_dispatch_running(
            sp,
            "a",
            dispatch_id="did-1",
            l3_pid=1234,
            starttime_ticks=42,
            boot_id="abc-boot",
        )
        state = read_state(sp)
        assert state is not None
        d = state.dispatches[0]
        assert d.status == DispatchStatus.RUNNING
        assert d.l3_pid == 1234
        assert d.l3_starttime_ticks == 42
        assert d.l3_boot_id == "abc-boot"

        raw = json.loads(sp.read_text())
        dispatch_raw = raw["dispatches"][0]
        assert dispatch_raw["l3_starttime_ticks"] == 42
        assert dispatch_raw["l3_boot_id"] == "abc-boot"

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
        assert d.l3_session_id == "sess-old"
        assert d.l3_session_log_dir == "/old/logs"
        assert d.l3_pid == 1234
        assert d.l3_starttime_ticks == 5678
        assert d.l3_boot_id == "boot-old"

    def test_dispatch_record_serializes_l3_field_names(self) -> None:
        """DispatchRecord.to_dict() must use l3_* field names."""
        d = DispatchRecord(name="x", l3_pid=42, l3_session_id="sess-new")
        raw = d.to_dict()
        assert "l3_pid" in raw
        assert "l3_session_id" in raw
        assert "l2_pid" not in raw
        assert "l2_session_id" not in raw

    def test_read_state_handles_v1_without_ticks(self, tmp_path: Path) -> None:
        """read_state on a v1 file missing l3_starttime_ticks/l3_boot_id returns defaults."""
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
        assert d.l3_pid == 9999
        assert d.l3_starttime_ticks == 0
        assert d.l3_boot_id == ""
