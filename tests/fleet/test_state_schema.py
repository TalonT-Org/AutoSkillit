"""Tests for DispatchRecord schema v2 fields and backward compatibility (Group J)."""

from __future__ import annotations

import dataclasses
import json
from pathlib import Path

import pytest

from autoskillit.fleet import (
    FLEET_STATE_SCHEMA_VERSION,
    DispatchRecord,
    DispatchStatus,
    DispatchTokenUsage,
    mark_dispatch_running,
    normalize_dispatch_token_usage,
    read_state,
    write_initial_state,
)

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

    def test_dispatch_record_has_dispatched_create_time(self) -> None:
        d = DispatchRecord(name="x")
        assert hasattr(d, "dispatched_create_time")
        assert d.dispatched_create_time == 0.0
        assert isinstance(d.dispatched_create_time, float)

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

    def test_schema_version_is_5(self) -> None:
        assert FLEET_STATE_SCHEMA_VERSION == 5

    def test_read_state_returns_none_on_version_mismatch(self, tmp_path: Path) -> None:
        """read_state returns None when schema_version is stale (v1)."""
        stale_payload = {
            "schema_version": 1,
            "campaign_id": "cmp",
            "campaign_name": "test",
            "manifest_path": "/m.yaml",
            "started_at": 1.0,
            "dispatches": [],
        }
        state_path = tmp_path / "state.json"
        state_path.write_text(json.dumps(stale_payload))
        state = read_state(state_path)
        assert state is None

    def test_read_state_returns_none_on_future_version(self, tmp_path: Path) -> None:
        """read_state returns None when schema_version is a future version."""
        future_payload = {
            "schema_version": 99,
            "campaign_id": "cmp",
            "campaign_name": "test",
            "manifest_path": "/m.yaml",
            "started_at": 1.0,
            "dispatches": [],
        }
        state_path = tmp_path / "state.json"
        state_path.write_text(json.dumps(future_payload))
        state = read_state(state_path)
        assert state is None

    def test_read_state_logs_drift_warning_on_mismatch(self, tmp_path: Path) -> None:
        """read_state logs a drift warning when schema_version is stale."""
        import structlog.testing

        stale_payload = {
            "schema_version": 3,
            "campaign_id": "cmp",
            "campaign_name": "test",
            "manifest_path": "/m.yaml",
            "started_at": 1.0,
            "dispatches": [],
        }
        state_path = tmp_path / "state.json"
        state_path.write_text(json.dumps(stale_payload))
        with structlog.testing.capture_logs() as cap:
            state = read_state(state_path)
        assert state is None
        drift_logs = [r for r in cap if "schema_drift" in r.get("event", "")]
        assert len(drift_logs) == 1

    def test_read_state_succeeds_on_current_version(self, tmp_path: Path) -> None:
        """read_state succeeds when schema_version matches FLEET_STATE_SCHEMA_VERSION."""
        state_path = tmp_path / "state.json"
        write_initial_state(state_path, "cmp-ok", "test", "/m.yaml", [DispatchRecord(name="d1")])
        state = read_state(state_path)
        assert state is not None
        assert state.schema_version == FLEET_STATE_SCHEMA_VERSION

    def test_read_state_rejects_stale_schema_version(self, tmp_path: Path) -> None:
        """read_state must reject schema v3 state files (stale version)."""
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
        assert state is None

    def test_dispatch_record_serializes_dispatched_field_names(self) -> None:
        """DispatchRecord.to_dict() must use dispatched_* field names."""
        d = DispatchRecord(name="x", dispatched_pid=42, dispatched_session_id="sess-new")
        raw = d.to_dict()
        assert "dispatched_pid" in raw
        assert "dispatched_session_id" in raw
        assert "l2_pid" not in raw
        assert "l2_session_id" not in raw

    def test_read_state_rejects_v3_schema(self, tmp_path: Path) -> None:
        """read_state must reject schema v3 state files (stale version)."""
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
        assert state is None

    def test_read_state_rejects_v1_schema(self, tmp_path: Path) -> None:
        """read_state must reject schema v1 state files (stale version)."""
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
        assert state is None


class TestCampaignIdField:
    def test_dispatch_record_has_campaign_id_default_empty(self) -> None:
        d = DispatchRecord(name="x")
        assert d.campaign_id == ""

    def test_dispatch_record_campaign_id_in_to_dict(self) -> None:
        d = DispatchRecord(name="x", campaign_id="cmp-42")
        raw = d.to_dict()
        assert raw["campaign_id"] == "cmp-42"

    def test_read_state_deserializes_campaign_id(self, tmp_path: Path) -> None:
        sp = tmp_path / "state.json"
        write_initial_state(
            sp, "cmp-99", "test", "/m.yaml", [DispatchRecord(name="a", campaign_id="cmp-99")]
        )
        state = read_state(sp)
        assert state is not None
        assert state.dispatches[0].campaign_id == "cmp-99"

    def test_read_state_defaults_campaign_id_when_absent(self, tmp_path: Path) -> None:
        sp = tmp_path / "state.json"
        payload = {
            "schema_version": 4,
            "campaign_id": "cmp-old",
            "campaign_name": "test",
            "manifest_path": "/m.yaml",
            "started_at": 1.0,
            "dispatches": [{"name": "a", "status": "pending"}],
        }
        sp.write_text(json.dumps(payload))
        state = read_state(sp)
        assert state is not None
        assert state.dispatches[0].campaign_id == ""

    def test_campaign_id_round_trip(self, tmp_path: Path) -> None:
        sp = tmp_path / "state.json"
        write_initial_state(
            sp, "cmp-rt", "test", "/m.yaml", [DispatchRecord(name="a", campaign_id="cmp-rt")]
        )
        state = read_state(sp)
        assert state is not None
        assert state.dispatches[0].campaign_id == "cmp-rt"


class TestNormalizeDispatchTokenUsage:
    def test_full_mapping(self) -> None:
        result = normalize_dispatch_token_usage(
            {
                "input_tokens": 10,
                "output_tokens": 5,
                "cache_creation_input_tokens": 2,
                "cache_read_input_tokens": 3,
            }
        )
        assert result == {"input": 10, "output": 5, "cache_creation": 2, "cache_read": 3}

    def test_empty_dict_returns_zeros(self) -> None:
        result = normalize_dispatch_token_usage({})
        assert result == {"input": 0, "output": 0, "cache_creation": 0, "cache_read": 0}

    def test_string_values_coerced_to_int(self) -> None:
        result = normalize_dispatch_token_usage(
            {
                "input_tokens": "7",
                "output_tokens": "3",
                "cache_creation_input_tokens": "1",
                "cache_read_input_tokens": "2",
            }
        )
        assert result == {"input": 7, "output": 3, "cache_creation": 1, "cache_read": 2}

    def test_result_unpacks_into_dispatch_token_usage(self) -> None:
        dtu = DispatchTokenUsage(
            **normalize_dispatch_token_usage(
                {
                    "input_tokens": 10,
                    "output_tokens": 5,
                    "cache_creation_input_tokens": 2,
                    "cache_read_input_tokens": 3,
                }
            )
        )
        assert dtu.input == 10
        assert dtu.output == 5
        assert dtu.cache_creation == 2
        assert dtu.cache_read == 3

    def test_importable_from_fleet_package(self) -> None:
        from autoskillit.fleet import normalize_dispatch_token_usage as imported

        assert callable(imported)

    def test_in_fleet_all(self) -> None:
        import autoskillit.fleet

        assert "normalize_dispatch_token_usage" in autoskillit.fleet.__all__


class TestDispatchRecordToDict:
    def test_dispatch_record_to_dict_all_fields(self) -> None:
        """DispatchRecord.to_dict() must return all expected keys."""
        from autoskillit.fleet import DispatchRecord

        record = DispatchRecord(name="test-job")
        d = record.to_dict()
        assert set(d.keys()) == {
            "name",
            "status",
            "dispatch_id",
            "campaign_id",
            "caller_session_id",
            "dispatched_session_id",
            "dispatched_session_log_dir",
            "dispatched_pid",
            "dispatched_starttime_ticks",
            "dispatched_boot_id",
            "dispatched_create_time",
            "reason",
            "kill_reason",
            "infra_exit_category",
            "token_usage",
            "started_at",
            "ended_at",
            "sidecar_path",
            "attempt_history",
        }

    def test_dispatch_record_to_dict_token_usage_is_shallow_copy(self) -> None:
        """DispatchRecord.to_dict() token_usage is a shallow copy, not a deep copy."""
        from autoskillit.fleet import DispatchRecord

        inner = {"nested": "value"}
        record = DispatchRecord(name="test-job", token_usage={"key": inner})
        d = record.to_dict()
        assert d["token_usage"] is not record.token_usage
        assert d["token_usage"]["key"] is inner

    def test_dispatch_record_to_dict_is_json_serializable(self) -> None:
        """DispatchRecord.to_dict() output must be JSON-serializable."""
        import json

        from autoskillit.fleet import DispatchRecord

        record = DispatchRecord(name="test-job", token_usage={"x": 1})
        d = record.to_dict()
        roundtripped = json.loads(json.dumps(d))
        assert roundtripped["name"] == "test-job"
        assert roundtripped["token_usage"] == {"x": 1}

    def test_to_dict_keys_match_dataclass_fields(self) -> None:
        """to_dict() must emit exactly the dataclass field names."""
        record = DispatchRecord(name="test")
        actual = set(record.to_dict().keys())
        expected = {f.name for f in dataclasses.fields(DispatchRecord)}
        assert actual == expected


class TestDispatchRecordSchemaV3:
    def test_normalize_maps_cache_keys_to_canonical_names(self) -> None:
        result = normalize_dispatch_token_usage(
            {
                "input_tokens": 10,
                "output_tokens": 5,
                "cache_creation_input_tokens": 200,
                "cache_read_input_tokens": 300,
            }
        )
        assert result["cache_creation"] == 200
        assert result["cache_read"] == 300
        assert "cache_creation_input_tokens" not in result
        assert "cache_read_input_tokens" not in result

    def test_normalize_defaults_missing_cache_keys_to_zero(self) -> None:
        result = normalize_dispatch_token_usage({"input_tokens": 10, "output_tokens": 5})
        assert result["cache_creation"] == 0
        assert result["cache_read"] == 0

    def test_campaign_id_roundtrip_via_dispatch_record(self, tmp_path: Path) -> None:
        sp = tmp_path / "state.json"
        write_initial_state(
            sp,
            "camp-xyz",
            "test",
            "/m.yaml",
            [DispatchRecord(name="a", campaign_id="camp-xyz")],
        )
        state = read_state(sp)
        assert state is not None
        assert state.dispatches[0].campaign_id == "camp-xyz"
        assert json.loads(sp.read_text())["dispatches"][0]["campaign_id"] == "camp-xyz"

    def test_dispatched_create_time_roundtrip(self, tmp_path: Path) -> None:
        sp = _make_state(tmp_path, "a")
        mark_dispatch_running(
            sp,
            "a",
            dispatch_id="did-1",
            dispatched_pid=9999,
            starttime_ticks=0,
            boot_id="",
            dispatched_create_time=1700000000.5,
        )
        state = read_state(sp)
        assert state is not None
        d = state.dispatches[0]
        assert d.dispatched_create_time == 1700000000.5

        raw = json.loads(sp.read_text())
        assert raw["dispatches"][0]["dispatched_create_time"] == 1700000000.5

    def test_v1_state_file_is_rejected(self, tmp_path: Path) -> None:
        """v1 state files are rejected (stale schema version)."""
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
        assert state is None


class TestAttemptHistoryFields:
    def test_attempt_history_in_to_dict(self) -> None:
        """to_dict includes attempt_history."""
        d = DispatchRecord(
            name="d1", attempt_history=[{"dispatch_id": "old", "status": "failure"}]
        )
        result = d.to_dict()
        assert "attempt_history" in result
        assert result["attempt_history"] == [{"dispatch_id": "old", "status": "failure"}]

    def test_attempt_history_from_dict_missing_defaults_empty(self) -> None:
        """from_dict handles missing attempt_history (v4 compat)."""
        d = DispatchRecord.from_dict({"name": "d1"})
        assert d.attempt_history == []

    def test_attempt_history_round_trips_through_to_dict(self) -> None:
        """attempt_history survives to_dict round-trip."""
        d = DispatchRecord(
            name="d1", attempt_history=[{"dispatch_id": "attempt-1", "status": "failure"}]
        )
        roundtripped = DispatchRecord.from_dict(d.to_dict())
        assert roundtripped.attempt_history == [{"dispatch_id": "attempt-1", "status": "failure"}]
