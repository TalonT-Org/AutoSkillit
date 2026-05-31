"""Tests for fleet state module (Group J)."""

from __future__ import annotations

import errno
import json
import os
import threading
from pathlib import Path
from unittest.mock import patch

import pytest

from autoskillit.fleet import (
    FLEET_HALTED_SENTINEL,
    DispatchRecord,
    DispatchStatus,
    append_dispatch_record,
    build_protected_campaign_ids,
    classify_stale_dispatch,
    has_failed_dispatch,
    mark_dispatch_resumable,
    mark_dispatch_running,
    read_all_campaign_captures,
    read_state,
    resume_campaign_from_state,
    write_captured_values,
    write_initial_state,
)
from autoskillit.fleet.state import FLEET_STATE_SCHEMA_VERSION
from autoskillit.fleet.state import _write_state as fleet_write_state

pytestmark = [pytest.mark.layer("fleet"), pytest.mark.small, pytest.mark.feature("fleet")]


def _make_dispatches(*names: str) -> list[DispatchRecord]:
    return [DispatchRecord(name=n) for n in names]


def _state_path(tmp_path: Path) -> Path:
    return tmp_path / "campaign" / "state.json"


class TestInitialState:
    def test_initial_state_file_has_all_dispatches_pending(self, tmp_path: Path) -> None:
        sp = _state_path(tmp_path)
        dispatches = _make_dispatches("a", "b", "c")
        write_initial_state(sp, "cid-1", "my-campaign", "/m.yaml", dispatches)

        state = read_state(sp)
        assert state is not None
        assert state.schema_version == 6
        assert state.campaign_id == "cid-1"
        assert state.campaign_name == "my-campaign"
        assert state.manifest_path == "/m.yaml"
        assert len(state.dispatches) == 3
        for d in state.dispatches:
            assert d.status == DispatchStatus.PENDING

    def test_read_state_round_trips_through_from_dict(self, tmp_path: Path) -> None:
        sp = _state_path(tmp_path)
        dispatches = [
            DispatchRecord(
                name="a",
                status=DispatchStatus.RUNNING,
                dispatch_id="d1",
                caller_session_id="c1",
                dispatched_session_id="s1",
                dispatched_session_log_dir="/log",
                dispatched_pid=1234,
                dispatched_starttime_ticks=9999,
                dispatched_boot_id="b1",
                reason="started",
                retry_reason="",
                infra_exit_category="",
                token_usage={"input": 100},
                started_at=1.0,
                ended_at=2.0,
                sidecar_path="/s.jsonl",
            ),
            DispatchRecord(name="b"),
        ]
        write_initial_state(sp, "cid", "cname", "/m.yaml", dispatches)
        state = read_state(sp)
        assert state is not None
        assert [d.name for d in state.dispatches] == ["a", "b"]
        a = state.dispatches[0]
        assert a.status == DispatchStatus.RUNNING
        assert a.dispatch_id == "d1"
        assert a.caller_session_id == "c1"
        assert a.dispatched_session_id == "s1"
        assert a.dispatched_session_log_dir == "/log"
        assert a.dispatched_pid == 1234
        assert a.dispatched_starttime_ticks == 9999
        assert a.dispatched_boot_id == "b1"
        assert a.reason == "started"
        assert a.token_usage == {"input": 100}
        assert a.started_at == 1.0
        assert a.ended_at == 2.0
        assert a.sidecar_path == "/s.jsonl"
        assert state.dispatches[1].status == DispatchStatus.PENDING


class TestDispatchRecordFromDict:
    def test_from_dict_canonical_fields(self) -> None:
        d = {
            "name": "build-docs",
            "status": "running",
            "dispatch_id": "abc",
            "caller_session_id": "c1",
            "dispatched_session_id": "s1",
            "dispatched_session_log_dir": "/log",
            "dispatched_pid": 1234,
            "dispatched_starttime_ticks": 9999,
            "dispatched_boot_id": "b1",
            "reason": "started",
            "retry_reason": "",
            "infra_exit_category": "",
            "token_usage": {"input": 100},
            "started_at": 1.0,
            "ended_at": 2.0,
            "sidecar_path": "/s.jsonl",
        }
        rec = DispatchRecord.from_dict(d)
        assert rec.name == "build-docs"
        assert rec.status == DispatchStatus.RUNNING
        assert rec.dispatch_id == "abc"
        assert rec.caller_session_id == "c1"
        assert rec.dispatched_session_id == "s1"
        assert rec.dispatched_session_log_dir == "/log"
        assert rec.dispatched_pid == 1234
        assert rec.dispatched_starttime_ticks == 9999
        assert rec.dispatched_boot_id == "b1"
        assert rec.reason == "started"
        assert rec.retry_reason == ""
        assert rec.infra_exit_category == ""
        assert rec.token_usage == {"input": 100}
        assert rec.started_at == 1.0
        assert rec.ended_at == 2.0
        assert rec.sidecar_path == "/s.jsonl"

    def test_from_dict_legacy_l2_fields(self) -> None:
        d = {
            "name": "t",
            "l2_session_id": "l2s",
            "l2_session_log_dir": "/l2",
            "l2_pid": 555,
            "l2_starttime_ticks": 111,
            "l2_boot_id": "l2b",
        }
        rec = DispatchRecord.from_dict(d)
        assert rec.dispatched_session_id == "l2s"
        assert rec.dispatched_session_log_dir == "/l2"
        assert rec.dispatched_pid == 555
        assert rec.dispatched_starttime_ticks == 111
        assert rec.dispatched_boot_id == "l2b"

    def test_from_dict_l3_takes_priority_over_l2(self) -> None:
        d = {
            "name": "t",
            "l3_session_id": "l3s",
            "l2_session_id": "l2s",
            "l3_session_log_dir": "/l3",
            "l2_session_log_dir": "/l2",
            "l3_pid": 333,
            "l2_pid": 222,
            "l3_starttime_ticks": 300,
            "l2_starttime_ticks": 200,
            "l3_boot_id": "l3b",
            "l2_boot_id": "l2b",
        }
        rec = DispatchRecord.from_dict(d)
        assert rec.dispatched_session_id == "l3s"
        assert rec.dispatched_session_log_dir == "/l3"
        assert rec.dispatched_pid == 333
        assert rec.dispatched_starttime_ticks == 300
        assert rec.dispatched_boot_id == "l3b"

    def test_from_dict_canonical_takes_priority_over_l3(self) -> None:
        d = {"name": "t", "dispatched_session_id": "canon", "l3_session_id": "l3s"}
        rec = DispatchRecord.from_dict(d)
        assert rec.dispatched_session_id == "canon"

    def test_from_dict_minimal_dict(self) -> None:
        rec = DispatchRecord.from_dict({"name": "minimal"})
        assert rec.name == "minimal"
        assert rec.status == DispatchStatus.PENDING
        assert rec.dispatched_pid == 0
        assert rec.token_usage == {}
        assert rec.sidecar_path is None

    def test_from_dict_pid_zero_not_falsy(self) -> None:
        d = {"name": "t", "dispatched_pid": 0, "l3_pid": 999}
        rec = DispatchRecord.from_dict(d)
        assert rec.dispatched_pid == 0

    def test_from_dict_starttime_ticks_zero_not_falsy(self) -> None:
        d = {"name": "t", "dispatched_starttime_ticks": 0, "l3_starttime_ticks": 888}
        rec = DispatchRecord.from_dict(d)
        assert rec.dispatched_starttime_ticks == 0

    def test_reaper_fields_round_trip_through_serialization(self) -> None:
        """reaper_reason and reaper_dispatch_id survive to_dict/from_dict (Test 1H)."""
        rec = DispatchRecord(
            name="t",
            reaper_reason="reaped_orphan",
            reaper_dispatch_id="xyz-456",
        )
        d = rec.to_dict()
        assert d["reaper_reason"] == "reaped_orphan"
        assert d["reaper_dispatch_id"] == "xyz-456"
        rec2 = DispatchRecord.from_dict(d)
        assert rec2.reaper_reason == "reaped_orphan"
        assert rec2.reaper_dispatch_id == "xyz-456"

    def test_reaper_fields_default_to_empty_string(self) -> None:
        """reaper fields default to empty string when absent from dict (Test 1H complement)."""
        rec = DispatchRecord.from_dict({"name": "t"})
        assert rec.reaper_reason == ""
        assert rec.reaper_dispatch_id == ""


class TestStateDecompositionImports:
    def test_state_types_importable(self) -> None:
        from autoskillit.fleet.state_types import (
            DispatchRecord,
            DispatchStatus,
        )

        assert DispatchStatus.PENDING == "pending"
        assert hasattr(DispatchRecord, "from_dict")

    def test_state_gates_importable(self) -> None:
        from autoskillit.fleet.state_gates import record_gate_outcome

        assert callable(record_gate_outcome)

    def test_state_recovery_importable(self) -> None:
        from autoskillit.fleet.state_recovery import (
            resume_campaign_from_state,
        )

        assert callable(resume_campaign_from_state)

    def test_backward_compat_from_state_module(self) -> None:
        from autoskillit.fleet.state import (
            read_state,
        )

        assert callable(read_state)


class TestAppendDispatchRecord:
    def test_append_dispatch_record_updates_status(self, tmp_path: Path) -> None:
        sp = _state_path(tmp_path)
        write_initial_state(sp, "cid", "camp", "/m.yaml", _make_dispatches("x", "y"))

        updated = DispatchRecord(name="x", status=DispatchStatus.SUCCESS)
        append_dispatch_record(sp, updated)

        state = read_state(sp)
        assert state is not None
        assert len(state.dispatches) == 2
        assert state.dispatches[0].name == "x"
        assert state.dispatches[0].status == DispatchStatus.SUCCESS


class TestAtomicWriteSurvivesPartialTmp:
    def test_atomic_write_survives_partial_tmp_file(self, tmp_path: Path) -> None:
        sp = _state_path(tmp_path)
        write_initial_state(sp, "cid", "camp", "/m.yaml", _make_dispatches("a"))

        original = sp.read_text(encoding="utf-8")
        real_replace = os.replace

        call_count = 0

        def failing_replace(src, dst):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise OSError("simulated crash")
            return real_replace(src, dst)

        with patch("autoskillit.core.io.os.replace", side_effect=failing_replace):
            with pytest.raises(OSError, match="simulated crash"):
                mark_dispatch_running(sp, "a", dispatch_id="d1", dispatched_pid=42)

        assert sp.read_text(encoding="utf-8") == original

        # Retry succeeds
        mark_dispatch_running(sp, "a", dispatch_id="d1", dispatched_pid=42)
        state = read_state(sp)
        assert state is not None
        assert state.dispatches[0].status == DispatchStatus.RUNNING


class TestResumeSkipsSuccessful:
    def test_resume_skips_successful_dispatches(self, tmp_path: Path) -> None:
        sp = _state_path(tmp_path)
        write_initial_state(sp, "cid", "camp", "/m.yaml", _make_dispatches("A", "B", "C"))
        append_dispatch_record(sp, DispatchRecord(name="A", status=DispatchStatus.SUCCESS))

        decision = resume_campaign_from_state(sp, continue_on_failure=True)
        assert decision is not None
        assert decision.next_dispatch_name == "B"
        assert "A" in decision.completed_dispatches_block


class TestResumeMarksRunningInterrupted:
    def test_resume_marks_running_as_interrupted(self, tmp_path: Path) -> None:
        sp = _state_path(tmp_path)
        write_initial_state(sp, "cid", "camp", "/m.yaml", _make_dispatches("A", "B", "C"))
        append_dispatch_record(sp, DispatchRecord(name="A", status=DispatchStatus.SUCCESS))
        mark_dispatch_running(sp, "B", dispatch_id="d-b", dispatched_pid=99)

        decision = resume_campaign_from_state(sp, continue_on_failure=True)
        assert decision is not None

        # B should now be interrupted on disk
        state = read_state(sp)
        assert state is not None
        b = next(d for d in state.dispatches if d.name == "B")
        assert b.status == DispatchStatus.INTERRUPTED

        # INTERRUPTED dispatches are skipped when searching for next_dispatch_name;
        # the first PENDING dispatch (C) is returned, not the interrupted one (B).
        assert decision.next_dispatch_name == "C"


class TestResumeRejectsHaltedOnFailure:
    def test_resume_rejects_if_halted_on_failure_with_no_continue_on_failure(
        self, tmp_path: Path
    ) -> None:
        sp = _state_path(tmp_path)
        write_initial_state(sp, "cid", "camp", "/m.yaml", _make_dispatches("A", "B", "C"))
        append_dispatch_record(sp, DispatchRecord(name="A", status=DispatchStatus.SUCCESS))
        append_dispatch_record(sp, DispatchRecord(name="B", status=DispatchStatus.FAILURE))

        decision = resume_campaign_from_state(sp, continue_on_failure=False)
        assert decision is not None
        assert decision.next_dispatch_name == ""
        assert decision.completed_dispatches_block == FLEET_HALTED_SENTINEL


class TestAtomicUnderConcurrentRead:
    def test_state_json_atomic_under_concurrent_read(self, tmp_path: Path) -> None:
        sp = _state_path(tmp_path)
        write_initial_state(sp, "cid", "camp", "/m.yaml", _make_dispatches("a"))

        barrier = threading.Barrier(2, timeout=5)
        errors: list[str] = []

        def writer():
            barrier.wait()
            mark_dispatch_running(sp, "a", dispatch_id="d1", dispatched_pid=42)

        def reader():
            barrier.wait()
            for _ in range(50):
                state = read_state(sp)
                if state is None:
                    errors.append("read_state returned None (corrupted)")
                    break

        t_write = threading.Thread(target=writer)
        t_read = threading.Thread(target=reader)
        t_write.start()
        t_read.start()
        t_write.join(timeout=5)
        t_read.join(timeout=5)

        assert not errors, f"Concurrent read errors: {errors}"


class TestWriteDiskFull:
    def test_state_write_disk_full(self, tmp_path: Path) -> None:
        sp = _state_path(tmp_path)
        write_initial_state(sp, "cid", "camp", "/m.yaml", _make_dispatches("a"))

        original = sp.read_text(encoding="utf-8")

        def enospc_replace(src, dst):
            raise OSError(errno.ENOSPC, "No space left on device")

        with patch("autoskillit.core.io.os.replace", side_effect=enospc_replace):
            with pytest.raises(OSError):
                mark_dispatch_running(sp, "a", dispatch_id="d1", dispatched_pid=42)

        assert sp.read_text(encoding="utf-8") == original


class TestReadStateRejectsCorrupted:
    def test_read_state_rejects_corrupted_json(self, tmp_path: Path) -> None:
        sp = _state_path(tmp_path)
        sp.parent.mkdir(parents=True, exist_ok=True)
        sp.write_bytes(b"not valid json {{{")

        result = read_state(sp)
        assert result is None


class TestCapturedValuesRoundTrip:
    def test_captured_values_round_trip(self, tmp_path: Path) -> None:
        sp = _state_path(tmp_path)
        write_initial_state(sp, "cid", "camp", "/m.yaml", _make_dispatches("a"))
        write_captured_values(sp, {"k": "v"})

        state = read_state(sp)
        assert state is not None
        assert state.captured_values == {"k": "v"}


class TestReadV2StateFileDefaultsCapturedValues:
    def test_read_v2_state_file_rejected(self, tmp_path: Path) -> None:
        """v2 state files are rejected (stale schema version)."""
        sp = _state_path(tmp_path)
        sp.parent.mkdir(parents=True, exist_ok=True)
        v2_data = {
            "schema_version": 2,
            "campaign_id": "cid",
            "campaign_name": "camp",
            "manifest_path": "/m.yaml",
            "started_at": 0.0,
            "dispatches": [],
        }
        sp.write_text(json.dumps(v2_data), encoding="utf-8")

        state = read_state(sp)
        assert state is None


class TestReadAllCampaignCaptures:
    def test_read_all_campaign_captures_merges_across_dispatches(self, tmp_path: Path) -> None:
        dispatches_dir = tmp_path / "dispatches"
        dispatches_dir.mkdir()

        for i, (key, val) in enumerate([("a", "1"), ("b", "2")]):
            sp = dispatches_dir / f"state{i}.json"
            write_initial_state(sp, "cid-merge", "camp", "/m.yaml", _make_dispatches(f"d{i}"))
            append_dispatch_record(sp, DispatchRecord(name=f"d{i}", status=DispatchStatus.SUCCESS))
            write_captured_values(sp, {key: val})

        result = read_all_campaign_captures(dispatches_dir, "cid-merge")
        assert result == {"a": "1", "b": "2"}

    def test_read_all_campaign_captures_ignores_non_success_dispatches(
        self, tmp_path: Path
    ) -> None:
        dispatches_dir = tmp_path / "dispatches"
        dispatches_dir.mkdir()

        sp = dispatches_dir / "failure.json"
        write_initial_state(sp, "cid-fail", "camp", "/m.yaml", _make_dispatches("d1"))
        append_dispatch_record(sp, DispatchRecord(name="d1", status=DispatchStatus.FAILURE))
        write_captured_values(sp, {"k": "should-not-appear"})

        result = read_all_campaign_captures(dispatches_dir, "cid-fail")
        assert result == {}

    def test_read_all_campaign_captures_empty_dir(self, tmp_path: Path) -> None:
        result = read_all_campaign_captures(tmp_path / "nonexistent", "any-id")
        assert result == {}


class TestGateDispatchSuccessIsSkippedOnResume:
    def test_gate_dispatch_success_is_skipped_on_resume(self, tmp_path: Path) -> None:
        sp = _state_path(tmp_path)
        write_initial_state(
            sp, "cid", "camp", "/m.yaml", _make_dispatches("gate-check", "phase-one")
        )
        append_dispatch_record(
            sp, DispatchRecord(name="gate-check", status=DispatchStatus.SUCCESS)
        )

        decision = resume_campaign_from_state(sp, continue_on_failure=False)
        assert decision is not None
        assert decision.next_dispatch_name == "phase-one"
        assert "gate-check" in decision.completed_dispatches_block


class TestGateDispatchFailureHaltsCampaign:
    def test_gate_dispatch_failure_halts_campaign(self, tmp_path: Path) -> None:
        sp = _state_path(tmp_path)
        write_initial_state(
            sp, "cid", "camp", "/m.yaml", _make_dispatches("gate-check", "phase-one")
        )
        append_dispatch_record(
            sp, DispatchRecord(name="gate-check", status=DispatchStatus.FAILURE)
        )

        decision = resume_campaign_from_state(sp, continue_on_failure=False)
        assert decision is not None
        assert decision.completed_dispatches_block == FLEET_HALTED_SENTINEL
        assert decision.next_dispatch_name == ""


class TestResumeSkipsAliveRunningDispatch:
    def test_resume_skips_running_dispatch_when_alive(self, tmp_path: Path, monkeypatch) -> None:
        """RUNNING dispatch with live process is NOT interrupted on resume."""
        sp = _state_path(tmp_path)
        record = DispatchRecord(
            name="issue-1",
            status=DispatchStatus.RUNNING,
            dispatched_pid=12345,
            dispatched_boot_id="abc",
            dispatched_starttime_ticks=999,
        )
        monkeypatch.setattr(
            "autoskillit.fleet.is_dispatch_session_alive",
            lambda r: True,
        )
        write_initial_state(sp, "c1", "test", "", [record])

        decision = resume_campaign_from_state(sp, continue_on_failure=False)

        state = read_state(sp)
        assert state is not None
        assert state.dispatches[0].status == DispatchStatus.RUNNING
        assert decision is not None
        assert decision.next_dispatch_name == ""


class TestResumeInterruptsStaleRunningDispatch:
    def test_resume_interrupts_stale_running_dispatch(self, tmp_path: Path, monkeypatch) -> None:
        """resume_campaign_from_state marks RUNNING as INTERRUPTED when process is dead."""
        sp = _state_path(tmp_path)
        record = DispatchRecord(
            name="issue-1",
            status=DispatchStatus.RUNNING,
            dispatched_pid=0,
            dispatched_boot_id="",
            dispatched_starttime_ticks=0,
        )
        monkeypatch.setattr(
            "autoskillit.fleet.is_dispatch_session_alive",
            lambda r: False,
        )
        write_initial_state(sp, "c1", "test", "", [record])

        resume_campaign_from_state(sp, continue_on_failure=False)

        state = read_state(sp)
        assert state is not None
        assert state.dispatches[0].status == DispatchStatus.INTERRUPTED


class TestResumeLockPreventsDoubleInterrupt:
    def test_resume_lock_prevents_concurrent_mutation(self, tmp_path: Path, monkeypatch) -> None:
        """Two concurrent resume_campaign_from_state calls serialize — no double-interrupt."""
        sp = _state_path(tmp_path)
        record = DispatchRecord(
            name="issue-1",
            status=DispatchStatus.RUNNING,
            dispatched_pid=0,
            dispatched_boot_id="",
            dispatched_starttime_ticks=0,
        )
        monkeypatch.setattr(
            "autoskillit.fleet.is_dispatch_session_alive",
            lambda r: False,
        )
        write_initial_state(sp, "c1", "test", "", [record])

        results: list[object] = []

        def _call() -> None:
            results.append(resume_campaign_from_state(sp, continue_on_failure=False))

        t1 = threading.Thread(target=_call)
        t2 = threading.Thread(target=_call)
        t1.start()
        t2.start()
        t1.join()
        t2.join()

        state = read_state(sp)
        assert state is not None
        assert state.dispatches[0].status == DispatchStatus.INTERRUPTED
        assert len(results) == 2


class TestResumeTransitionsRunningToResumable:
    def test_resume_marks_running_as_resumable_when_sidecar_exists(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        sp = _state_path(tmp_path)
        write_initial_state(sp, "c1", "myCampaign", "manifest.yaml", _make_dispatches("impl"))
        sidecar_file = sp.parent / "d1111_issues.jsonl"
        mark_dispatch_running(
            sp, "impl", dispatch_id="d1111", dispatched_pid=999, sidecar_path=str(sidecar_file)
        )
        sidecar_file.write_text(
            '{"issue_url":"https://github.com/o/r/issues/1","status":"completed","ts":"2026-01-01T00:00:00"}\n'
        )
        monkeypatch.setattr("autoskillit.fleet.is_dispatch_session_alive", lambda _: False)

        decision = resume_campaign_from_state(sp, continue_on_failure=False)

        state = read_state(sp)
        assert state is not None
        latest = next(d for d in reversed(state.dispatches) if d.name == "impl")
        assert latest.status == DispatchStatus.RESUMABLE
        assert latest.sidecar_path is not None
        assert decision is not None
        assert decision.is_resumable is True
        assert decision.next_dispatch_name == "impl"


class TestResumeTransitionsRunningToInterruptedNoSidecar:
    def test_resume_marks_running_as_interrupted_when_no_sidecar(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        sp = _state_path(tmp_path)
        write_initial_state(sp, "c1", "myCampaign", "manifest.yaml", _make_dispatches("impl"))
        mark_dispatch_running(sp, "impl", dispatch_id="d1111", dispatched_pid=999)
        monkeypatch.setattr("autoskillit.fleet.is_dispatch_session_alive", lambda _: False)

        resume_campaign_from_state(sp, continue_on_failure=False)

        state = read_state(sp)
        assert state is not None
        latest = next(d for d in reversed(state.dispatches) if d.name == "impl")
        assert latest.status == DispatchStatus.INTERRUPTED


class TestResumeTransitionsRunningToInterruptedCorruptSidecar:
    def test_resume_marks_running_as_interrupted_when_sidecar_corrupt(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        sp = _state_path(tmp_path)
        write_initial_state(sp, "c1", "myCampaign", "manifest.yaml", _make_dispatches("impl"))
        sidecar_file = sp.parent / "d1111_issues.jsonl"
        mark_dispatch_running(
            sp, "impl", dispatch_id="d1111", dispatched_pid=999, sidecar_path=str(sidecar_file)
        )
        sidecar_file.write_text("{not valid json{{{\n")
        monkeypatch.setattr("autoskillit.fleet.is_dispatch_session_alive", lambda _: False)

        resume_campaign_from_state(sp, continue_on_failure=False)

        state = read_state(sp)
        assert state is not None
        latest = next(d for d in reversed(state.dispatches) if d.name == "impl")
        assert latest.status == DispatchStatus.INTERRUPTED


class TestResumeEmptySidecarIsResumable:
    def test_resume_marks_running_as_resumable_when_sidecar_empty(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        sp = _state_path(tmp_path)
        write_initial_state(sp, "c1", "myCampaign", "manifest.yaml", _make_dispatches("impl"))
        sidecar_file = sp.parent / "d1111_issues.jsonl"
        mark_dispatch_running(
            sp, "impl", dispatch_id="d1111", dispatched_pid=999, sidecar_path=str(sidecar_file)
        )
        sidecar_file.write_text("")
        monkeypatch.setattr("autoskillit.fleet.is_dispatch_session_alive", lambda _: False)

        decision = resume_campaign_from_state(sp, continue_on_failure=False)

        state = read_state(sp)
        assert state is not None
        latest = next(d for d in reversed(state.dispatches) if d.name == "impl")
        assert latest.status == DispatchStatus.RESUMABLE
        assert decision is not None
        assert decision.is_resumable is True


class TestResumableSelectedBeforePending:
    def test_resumable_selected_as_next_before_pending(self, tmp_path: Path) -> None:
        sp = _state_path(tmp_path)
        write_initial_state(
            sp, "c1", "myCampaign", "manifest.yaml", _make_dispatches("impl-1", "impl-2")
        )
        mark_dispatch_running(sp, "impl-1", dispatch_id="d1111", dispatched_pid=999)
        # Non-existent sidecar is intentional: test covers selection ordering only.
        mark_dispatch_resumable(sp, "impl-1", sidecar_path=str(sp.parent / "d1111_issues.jsonl"))

        decision = resume_campaign_from_state(sp, continue_on_failure=False)

        assert decision is not None
        assert decision.next_dispatch_name == "impl-1"
        assert decision.is_resumable is True

    def test_resume_decision_carries_dispatched_session_id(self, tmp_path: Path) -> None:
        sp = _state_path(tmp_path)
        write_initial_state(sp, "c1", "myCampaign", "manifest.yaml", _make_dispatches("impl-1"))
        mark_dispatch_running(sp, "impl-1", dispatch_id="d2222", dispatched_pid=888)
        append_dispatch_record(
            sp,
            DispatchRecord(
                name="impl-1",
                status=DispatchStatus.RESUMABLE,
                dispatch_id="d2222",
                dispatched_session_id="sess-xyz-test",
                sidecar_path=str(sp.parent / "d2222_issues.jsonl"),
            ),
        )

        decision = resume_campaign_from_state(sp, continue_on_failure=False)

        assert decision is not None
        assert decision.is_resumable is True
        assert decision.dispatched_session_id == "sess-xyz-test"


class TestResumableStateTransitionsValid:
    def test_resumable_valid_transitions(self, tmp_path: Path) -> None:
        for next_status in [
            DispatchStatus.RUNNING,
            DispatchStatus.SUCCESS,
            DispatchStatus.FAILURE,
            DispatchStatus.INTERRUPTED,
        ]:
            sp = _state_path(tmp_path / next_status.value)
            write_initial_state(sp, "c1", "camp", "m.yaml", _make_dispatches("impl"))
            mark_dispatch_running(sp, "impl", dispatch_id="d1", dispatched_pid=1)
            mark_dispatch_resumable(sp, "impl", sidecar_path=str(tmp_path / "s.jsonl"))
            append_dispatch_record(sp, DispatchRecord(name="impl", status=next_status))
            state = read_state(sp)
            assert state is not None
            matches = [d for d in reversed(state.dispatches) if d.name == "impl"]
            assert matches, f"no dispatch named 'impl' found for status {next_status}"
            assert matches[0].status == next_status


class TestMarkDispatchResumable:
    def test_mark_dispatch_resumable_sets_sidecar_path(self, tmp_path: Path) -> None:
        sp = _state_path(tmp_path)
        write_initial_state(sp, "c1", "myCampaign", "manifest.yaml", _make_dispatches("impl"))
        expected_sidecar = str(sp.parent / "d1111_issues.jsonl")
        mark_dispatch_running(sp, "impl", dispatch_id="d1111", dispatched_pid=999)

        mark_dispatch_resumable(sp, "impl", sidecar_path=expected_sidecar)

        state = read_state(sp)
        assert state is not None
        latest = next(d for d in reversed(state.dispatches) if d.name == "impl")
        assert latest.status == DispatchStatus.RESUMABLE
        assert latest.sidecar_path == expected_sidecar


class TestSidecarPathSetOnMarkRunning:
    def test_sidecar_path_set_when_mark_dispatch_running(self, tmp_path: Path) -> None:
        sp = _state_path(tmp_path)
        write_initial_state(sp, "c1", "myCampaign", "manifest.yaml", _make_dispatches("impl"))
        expected_sidecar = str(sp.parent / "d1111_issues.jsonl")

        mark_dispatch_running(
            sp,
            "impl",
            dispatch_id="d1111",
            dispatched_pid=999,
            sidecar_path=expected_sidecar,
        )

        state = read_state(sp)
        assert state is not None
        latest = next(d for d in reversed(state.dispatches) if d.name == "impl")
        assert latest.sidecar_path == expected_sidecar


class TestAppendDispatchRecordIllegalTransition:
    def test_success_to_running_raises_valueerror(self, tmp_path: Path) -> None:
        sp = _state_path(tmp_path)
        write_initial_state(sp, "cid", "camp", "/m.yaml", _make_dispatches("A"))
        append_dispatch_record(sp, DispatchRecord(name="A", status=DispatchStatus.SUCCESS))
        with pytest.raises(ValueError, match="Invalid transition"):
            append_dispatch_record(sp, DispatchRecord(name="A", status=DispatchStatus.RUNNING))
        state = read_state(sp)
        assert state is not None
        assert state.dispatches[0].status == DispatchStatus.SUCCESS

    def test_pending_to_interrupted_raises_valueerror(self, tmp_path: Path) -> None:
        sp = _state_path(tmp_path)
        write_initial_state(sp, "cid", "camp", "/m.yaml", _make_dispatches("A"))
        with pytest.raises(ValueError, match="Invalid transition"):
            append_dispatch_record(sp, DispatchRecord(name="A", status=DispatchStatus.INTERRUPTED))

    def test_running_to_success_succeeds(self, tmp_path: Path) -> None:
        sp = _state_path(tmp_path)
        write_initial_state(sp, "cid", "camp", "/m.yaml", _make_dispatches("A"))
        mark_dispatch_running(sp, "A", dispatch_id="d-a", dispatched_pid=99)
        append_dispatch_record(sp, DispatchRecord(name="A", status=DispatchStatus.SUCCESS))
        state = read_state(sp)
        assert state is not None
        latest = next(d for d in reversed(state.dispatches) if d.name == "A")
        assert latest.status == DispatchStatus.SUCCESS


class TestRefusedDispatchVisibleInBlock:
    def test_refused_dispatch_visible_next_is_b(self, tmp_path: Path) -> None:
        sp = _state_path(tmp_path)
        write_initial_state(sp, "cid", "camp", "/m.yaml", _make_dispatches("A", "B"))
        from autoskillit.fleet import upsert_dispatch_record_by_name

        upsert_dispatch_record_by_name(sp, DispatchRecord(name="A", status=DispatchStatus.REFUSED))
        decision = resume_campaign_from_state(sp, continue_on_failure=True)
        assert decision is not None
        assert decision.next_dispatch_name == "B"
        assert "A" in decision.completed_dispatches_block
        assert "refused" in decision.completed_dispatches_block.lower()
        state = read_state(sp)
        assert state is not None
        a = next(d for d in state.dispatches if d.name == "A")
        assert a.status == DispatchStatus.REFUSED


class TestResumeShowsReleasedInBlock:
    def test_released_dispatch_visible_next_is_b(self, tmp_path: Path) -> None:
        sp = _state_path(tmp_path)
        write_initial_state(sp, "cid", "camp", "/m.yaml", _make_dispatches("A", "B"))
        append_dispatch_record(sp, DispatchRecord(name="A", status=DispatchStatus.RELEASED))
        decision = resume_campaign_from_state(sp, continue_on_failure=True)
        assert decision is not None
        assert decision.next_dispatch_name == "B"
        assert "A" in decision.completed_dispatches_block
        assert "released" in decision.completed_dispatches_block.lower()


class TestInterruptedDispatchVisibleInBlock:
    def test_interrupted_dispatch_visible_next_is_c(self, tmp_path: Path) -> None:
        sp = _state_path(tmp_path)
        write_initial_state(sp, "cid", "camp", "/m.yaml", _make_dispatches("A", "B", "C"))
        append_dispatch_record(sp, DispatchRecord(name="A", status=DispatchStatus.SUCCESS))
        from autoskillit.fleet import upsert_dispatch_record_by_name

        upsert_dispatch_record_by_name(
            sp, DispatchRecord(name="B", status=DispatchStatus.INTERRUPTED)
        )
        decision = resume_campaign_from_state(sp, continue_on_failure=True)
        assert decision is not None
        assert decision.next_dispatch_name == "C"
        assert "A" in decision.completed_dispatches_block
        assert "B" in decision.completed_dispatches_block
        assert "interrupted" in decision.completed_dispatches_block.lower()


class TestResumeIncludesRunningAliveInBlock:
    def test_running_alive_dispatch_visible_in_completed_block(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        sp = _state_path(tmp_path)
        record_a = DispatchRecord(name="A", status=DispatchStatus.SUCCESS)
        record_b = DispatchRecord(
            name="B",
            status=DispatchStatus.RUNNING,
            dispatched_pid=12345,
            dispatched_boot_id="abc",
            dispatched_starttime_ticks=999,
        )
        record_c = DispatchRecord(name="C", status=DispatchStatus.PENDING)
        monkeypatch.setattr(
            "autoskillit.fleet.is_dispatch_session_alive",
            lambda r: True,
        )
        write_initial_state(sp, "cid", "camp", "/m.yaml", [record_a, record_b, record_c])
        decision = resume_campaign_from_state(sp, continue_on_failure=True)
        assert decision is not None
        assert "B" in decision.completed_dispatches_block
        assert "running" in decision.completed_dispatches_block.lower()
        assert decision.next_dispatch_name == "C"


class TestWriteCapturedValuesCorruptStateNoOp:
    def test_invalid_json_returns_none_file_unchanged(self, tmp_path: Path) -> None:
        sp = _state_path(tmp_path)
        sp.parent.mkdir(parents=True, exist_ok=True)
        sp.write_text("not-valid-json{{", encoding="utf-8")
        original = sp.read_text(encoding="utf-8")
        write_captured_values(sp, {"key": "val"})
        assert sp.read_text(encoding="utf-8") == original


class TestWriteCapturedValuesRaisesOnMissingFile:
    def test_missing_file_raises_file_not_found_error(self, tmp_path: Path) -> None:
        sp = tmp_path / "nonexistent" / "state.json"
        with pytest.raises(FileNotFoundError):
            write_captured_values(sp, {"key": "val"})


class TestReadAllCampaignCapturesMixedSuccessFailure:
    def test_mixed_success_failure_returns_empty(self, tmp_path: Path) -> None:
        d = tmp_path / "dispatches"
        d.mkdir()
        state = {
            "campaign_id": "c1",
            "captured_values": {"k": "v"},
            "dispatches": [
                {"name": "A", "status": DispatchStatus.SUCCESS},
                {"name": "B", "status": DispatchStatus.FAILURE},
            ],
        }
        (d / "state.json").write_text(json.dumps(state), encoding="utf-8")
        result = read_all_campaign_captures(d, "c1")
        assert result == {}


class TestHasFailedDispatchReasonAware:
    def test_no_result_block_failure_does_not_halt_campaign(self, tmp_path: Path) -> None:
        """has_failed_dispatch returns False when only FAILURE is fleet_l3_no_result_block."""
        sp = _state_path(tmp_path)
        write_initial_state(sp, "cid", "camp", "/m.yaml", _make_dispatches("d1"))
        append_dispatch_record(
            sp,
            DispatchRecord(
                name="d1",
                status=DispatchStatus.FAILURE,
                reason="fleet_l3_no_result_block",
            ),
        )
        assert has_failed_dispatch(sp) is False

    def test_logic_failure_halts_campaign(self, tmp_path: Path) -> None:
        """has_failed_dispatch returns True for a completed_clean-based FAILURE."""
        sp = _state_path(tmp_path)
        write_initial_state(sp, "cid", "camp", "/m.yaml", _make_dispatches("d1"))
        append_dispatch_record(
            sp,
            DispatchRecord(
                name="d1",
                status=DispatchStatus.FAILURE,
                reason="task-failed",
            ),
        )
        assert has_failed_dispatch(sp) is True

    def test_mixed_infrastructure_and_logic_failure_halts(self, tmp_path: Path) -> None:
        """has_failed_dispatch returns True if ANY non-infrastructure FAILURE exists."""
        sp = _state_path(tmp_path)
        write_initial_state(sp, "cid", "camp", "/m.yaml", _make_dispatches("d1", "d2"))
        append_dispatch_record(
            sp,
            DispatchRecord(
                name="d1", status=DispatchStatus.FAILURE, reason="fleet_l3_no_result_block"
            ),
        )
        append_dispatch_record(
            sp,
            DispatchRecord(name="d2", status=DispatchStatus.FAILURE, reason="task-failed"),
        )
        assert has_failed_dispatch(sp) is True


class TestHasCompletedDispatch:
    def test_has_completed_dispatch_returns_true_for_success(self, tmp_path: Path) -> None:
        """has_completed_dispatch returns True when the named dispatch has SUCCESS status."""
        from autoskillit.fleet.state_recovery import has_completed_dispatch

        sp = _state_path(tmp_path)
        write_initial_state(
            sp,
            "cid",
            "camp",
            "/m.yaml",
            [DispatchRecord(name="d1", status=DispatchStatus.SUCCESS)],
        )
        assert has_completed_dispatch(sp, "d1") is True

    def test_has_completed_dispatch_returns_false_for_non_success(self, tmp_path: Path) -> None:
        """has_completed_dispatch returns False for RESUMABLE, PENDING, FAILURE, etc."""
        from autoskillit.fleet.state_recovery import has_completed_dispatch

        sp = _state_path(tmp_path)
        write_initial_state(sp, "cid", "camp", "/m.yaml", _make_dispatches("d1", "d2", "d3"))
        append_dispatch_record(
            sp, DispatchRecord(name="d2", status=DispatchStatus.FAILURE, reason="task-failed")
        )
        assert has_completed_dispatch(sp, "d1") is False
        assert has_completed_dispatch(sp, "d2") is False
        assert has_completed_dispatch(sp, "d3") is False

    def test_has_completed_dispatch_returns_false_for_missing_state(self, tmp_path: Path) -> None:
        """has_completed_dispatch returns False when state file is missing (fail-open)."""
        from autoskillit.fleet.state_recovery import has_completed_dispatch

        sp = tmp_path / "nonexistent" / "state.json"
        assert has_completed_dispatch(sp, "any-dispatch") is False


class TestRetryReasonPropagation:
    def test_retry_reason_stored_in_dispatch_record(self, tmp_path: Path) -> None:
        sp = _state_path(tmp_path)
        write_initial_state(sp, "cid", "camp", "/m.yaml", _make_dispatches("x"))
        record = DispatchRecord(
            name="x",
            status=DispatchStatus.FAILURE,
            retry_reason="idle_stall",
        )
        append_dispatch_record(sp, record)
        state = read_state(sp)
        assert state is not None
        assert state.dispatches[0].retry_reason == "idle_stall"

    def test_retry_reason_defaults_empty(self) -> None:
        record = DispatchRecord(name="x")
        assert record.retry_reason == ""

    def test_retry_reason_round_trips_through_json(self, tmp_path: Path) -> None:
        sp = _state_path(tmp_path)
        write_initial_state(sp, "cid", "camp", "/m.yaml", _make_dispatches("x"))
        record = DispatchRecord(
            name="x",
            status=DispatchStatus.FAILURE,
            retry_reason="context_exhausted",
            infra_exit_category="context_exhausted",
        )
        append_dispatch_record(sp, record)
        raw = sp.read_text(encoding="utf-8")
        assert '"retry_reason"' in raw
        assert '"infra_exit_category"' in raw
        state = read_state(sp)
        assert state is not None
        assert state.dispatches[0].retry_reason == "context_exhausted"
        assert state.dispatches[0].infra_exit_category == "context_exhausted"

    def test_resume_decision_includes_kill_reason(self, tmp_path: Path) -> None:
        sp = _state_path(tmp_path)
        sidecar = tmp_path / "sidecar.jsonl"
        sidecar.write_text("")
        write_initial_state(sp, "cid", "camp", "/m.yaml", _make_dispatches("x"))
        mark_dispatch_running(sp, "x", dispatch_id="d1", dispatched_pid=42)
        mark_dispatch_resumable(sp, "x", sidecar_path=str(sidecar))
        data = json.loads(sp.read_text())
        for d in data["dispatches"]:
            if d["name"] == "x":
                d["retry_reason"] = "idle_stall"
                d["infra_exit_category"] = ""
                d["dispatched_session_id"] = "sess-1"
        sp.write_text(json.dumps(data))
        decision = resume_campaign_from_state(sp, continue_on_failure=False)
        assert decision is not None
        assert decision.is_resumable is True
        assert decision.retry_reason == "idle_stall"

    def test_dispatch_record_retry_reason_field(self) -> None:
        """DispatchRecord.retry_reason stores RetryReason values."""
        record = DispatchRecord(name="test", retry_reason="resume")
        assert record.retry_reason == "resume"
        assert not hasattr(record, "kill_reason")

    def test_dispatch_record_from_dict_reads_legacy_kill_reason_key(self) -> None:
        """Old state files with 'kill_reason' JSON key deserialize into retry_reason."""
        raw: dict[str, object] = {"name": "test", "status": "running", "kill_reason": "stale"}
        record = DispatchRecord.from_dict(raw)
        assert record.retry_reason == "stale"


class TestOrchestratorSessionIdRoundTrip:
    def test_orchestrator_session_id_round_trip(self, tmp_path: Path) -> None:
        sp = _state_path(tmp_path)
        write_initial_state(sp, "cid", "camp", "/m.yaml", _make_dispatches("a"))
        from autoskillit.fleet import update_orchestrator_session_id

        update_orchestrator_session_id(sp, "prior-session-abc")

        state = read_state(sp)
        assert state is not None
        assert state.orchestrator_session_id == "prior-session-abc"

    def test_orchestrator_session_id_defaults_empty_on_v4_state(self, tmp_path: Path) -> None:
        sp = _state_path(tmp_path)
        sp.parent.mkdir(parents=True, exist_ok=True)
        v4_data = {
            "schema_version": 4,
            "campaign_id": "cid",
            "campaign_name": "camp",
            "manifest_path": "/m.yaml",
            "started_at": 0.0,
            "dispatches": [],
        }
        sp.write_text(json.dumps(v4_data), encoding="utf-8")

        state = read_state(sp)
        assert state is not None
        assert state.orchestrator_session_id == ""

    def test_orchestrator_session_id_written_to_state_file(self, tmp_path: Path) -> None:
        sp = _state_path(tmp_path)
        write_initial_state(sp, "cid", "camp", "/m.yaml", _make_dispatches("a"))
        from autoskillit.fleet import update_orchestrator_session_id

        update_orchestrator_session_id(sp, "sess-xyz-789")

        raw = json.loads(sp.read_text(encoding="utf-8"))
        assert raw.get("orchestrator_session_id") == "sess-xyz-789"


class TestUpdateOrchestratorSessionId:
    def test_update_orchestrator_session_id_writes_atomically(self, tmp_path: Path) -> None:
        sp = _state_path(tmp_path)
        write_initial_state(sp, "cid", "camp", "/m.yaml", _make_dispatches("a"))

        original = sp.read_text(encoding="utf-8")
        real_replace = os.replace
        call_count = 0

        def failing_replace(src, dst):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise OSError("simulated crash")
            return real_replace(src, dst)

        from autoskillit.fleet import update_orchestrator_session_id

        with patch("autoskillit.core.io.os.replace", side_effect=failing_replace):
            with pytest.raises(OSError, match="simulated crash"):
                update_orchestrator_session_id(sp, "new-session-id")

        assert sp.read_text(encoding="utf-8") == original

        update_orchestrator_session_id(sp, "new-session-id")
        state = read_state(sp)
        assert state is not None
        assert state.orchestrator_session_id == "new-session-id"


class TestClassifyStaleDispatch:
    """Tests for classify_stale_dispatch covering OSError and kill_reason branches."""

    def test_sidecar_oserror_falls_back_to_interrupted(self, tmp_path: Path) -> None:
        """classify_stale_dispatch returns INTERRUPTED when sidecar.read_text raises OSError."""
        sidecar = tmp_path / "sidecar.jsonl"
        sidecar.write_text("line1")
        record = DispatchRecord(
            name="d1",
            status=DispatchStatus.RUNNING,
            sidecar_path=str(sidecar),
            retry_reason="idle_stall",
        )
        _orig_read_text = Path.read_text

        def _raise_for_sidecar(self: Path, *args: object, **kwargs: object) -> str:
            if self == sidecar:
                raise OSError("vanished")
            return _orig_read_text(self, *args, **kwargs)  # type: ignore[arg-type]

        with patch.object(Path, "read_text", _raise_for_sidecar):
            status, sidecar_path_out = classify_stale_dispatch(record)
        assert status == DispatchStatus.INTERRUPTED
        assert sidecar_path_out == ""

    def test_context_exhausted_kill_reason_returns_interrupted(self, tmp_path: Path) -> None:
        """kill_reason=resume + infra_exit_category=context_exhausted → INTERRUPTED."""
        sidecar = tmp_path / "sidecar.jsonl"
        sidecar.write_text("")  # empty sidecar → raw_lines=[] → not raw_lines=True
        record = DispatchRecord(
            name="d1",
            status=DispatchStatus.RUNNING,
            sidecar_path=str(sidecar),
            retry_reason="resume",
            infra_exit_category="context_exhausted",
        )
        status, sidecar_path_out = classify_stale_dispatch(record)
        assert status == DispatchStatus.INTERRUPTED
        assert sidecar_path_out == ""

    def test_idle_stall_kill_reason_returns_interrupted(self, tmp_path: Path) -> None:
        """kill_reason=idle_stall (abandon) + empty sidecar → INTERRUPTED."""
        sidecar = tmp_path / "sidecar.jsonl"
        sidecar.write_text("")  # empty sidecar → raw_lines=[] → not raw_lines=True
        record = DispatchRecord(
            name="d1",
            status=DispatchStatus.RUNNING,
            sidecar_path=str(sidecar),
            retry_reason="idle_stall",
            infra_exit_category="",
        )
        status, sidecar_path_out = classify_stale_dispatch(record)
        assert status == DispatchStatus.INTERRUPTED
        assert sidecar_path_out == ""

    def test_no_sidecar_returns_interrupted(self) -> None:
        """No sidecar path → INTERRUPTED fallback."""
        record = DispatchRecord(
            name="d1",
            status=DispatchStatus.RUNNING,
            sidecar_path=None,
        )
        status, sidecar_path_out = classify_stale_dispatch(record)
        assert status == DispatchStatus.INTERRUPTED
        assert sidecar_path_out == ""


class TestWriteStateSchemaVersionPinning:
    def test_write_state_pins_schema_version_to_constant(self, tmp_path: Path) -> None:
        """_write_state must stamp FLEET_STATE_SCHEMA_VERSION, not state.schema_version."""
        sp = _state_path(tmp_path)
        write_initial_state(sp, "cid", "camp", "/m.yaml", _make_dispatches("a"))
        state = read_state(sp)
        assert state is not None
        # Artificially corrupt the in-memory schema_version
        bad_state = state
        bad_state.schema_version = 2  # type: ignore[attr-defined]
        fleet_write_state(sp, bad_state)
        raw = json.loads(sp.read_text())
        assert raw["schema_version"] == FLEET_STATE_SCHEMA_VERSION

    def test_round_trip_version_upgrade(self, tmp_path: Path) -> None:
        """A v5 state file written back after mutation stays v5."""
        sp = _state_path(tmp_path)
        write_initial_state(sp, "cid", "camp", "/m.yaml", _make_dispatches("a"))
        state = read_state(sp)
        assert state is not None
        assert state.schema_version == FLEET_STATE_SCHEMA_VERSION
        mark_dispatch_running(sp, "a", dispatch_id="d1", dispatched_pid=42)
        raw = json.loads(sp.read_text())
        assert raw["schema_version"] == FLEET_STATE_SCHEMA_VERSION


class TestBuildProtectedCampaignIdsSchemaValidation:
    def test_build_protected_campaign_ids_skips_stale_version(self, tmp_path: Path) -> None:
        """build_protected_campaign_ids must skip files with stale schema_version."""
        dispatches_dir = tmp_path / ".autoskillit" / "temp" / "dispatches"
        dispatches_dir.mkdir(parents=True)
        stale_file = dispatches_dir / "cid-stale.json"
        stale_file.write_text(
            json.dumps(
                {
                    "schema_version": 2,  # stale
                    "campaign_id": "cid-stale",
                    "campaign_name": "test",
                    "manifest_path": "/m.yaml",
                    "started_at": 0.0,
                    "dispatches": [{"name": "d1", "status": "running"}],
                }
            ),
            encoding="utf-8",
        )
        result = build_protected_campaign_ids(tmp_path)
        assert "cid-stale" not in result

    def test_build_protected_campaign_ids_includes_current_version(self, tmp_path: Path) -> None:
        """build_protected_campaign_ids includes files with current schema_version."""
        dispatches_dir = tmp_path / ".autoskillit" / "temp" / "dispatches"
        dispatches_dir.mkdir(parents=True)
        current_file = dispatches_dir / "cid-current.json"
        current_file.write_text(
            json.dumps(
                {
                    "schema_version": FLEET_STATE_SCHEMA_VERSION,
                    "campaign_id": "cid-current",
                    "campaign_name": "test",
                    "manifest_path": "/m.yaml",
                    "started_at": 0.0,
                    "dispatches": [{"name": "d1", "status": "running"}],
                }
            ),
            encoding="utf-8",
        )
        result = build_protected_campaign_ids(tmp_path)
        assert "cid-current" in result


class TestReadAllCampaignCapturesSchemaValidation:
    def test_read_all_campaign_captures_skips_stale_version(self, tmp_path: Path) -> None:
        """read_all_campaign_captures must skip files with stale schema_version."""
        dispatches_dir = tmp_path / "dispatches"
        dispatches_dir.mkdir()
        stale_file = dispatches_dir / "cid-stale.json"
        stale_file.write_text(
            json.dumps(
                {
                    "schema_version": 2,  # stale
                    "campaign_id": "cid-stale",
                    "campaign_name": "test",
                    "manifest_path": "/m.yaml",
                    "started_at": 0.0,
                    "dispatches": [{"name": "d1", "status": "success"}],
                    "captured_values": {"key": "value"},
                }
            ),
            encoding="utf-8",
        )
        result = read_all_campaign_captures(dispatches_dir, "cid-stale")
        assert result == {}

    def test_read_all_campaign_captures_includes_current_version(self, tmp_path: Path) -> None:
        """read_all_campaign_captures includes files with current schema_version."""
        dispatches_dir = tmp_path / "dispatches"
        dispatches_dir.mkdir()
        current_file = dispatches_dir / "cid-current.json"
        current_file.write_text(
            json.dumps(
                {
                    "schema_version": FLEET_STATE_SCHEMA_VERSION,
                    "campaign_id": "cid-current",
                    "campaign_name": "test",
                    "manifest_path": "/m.yaml",
                    "started_at": 0.0,
                    "dispatches": [{"name": "d1", "status": "success"}],
                    "captured_values": {"key": "value"},
                }
            ),
            encoding="utf-8",
        )
        result = read_all_campaign_captures(dispatches_dir, "cid-current")
        assert result == {"key": "value"}


class TestCampaignEndedAt:
    def test_campaign_ended_at_defaults_to_zero(self, tmp_path: Path) -> None:
        """CampaignState.ended_at defaults to 0.0 on fresh state file."""
        sp = _state_path(tmp_path)
        write_initial_state(sp, "cid", "camp", "/m.yaml", _make_dispatches("d1"))
        state = read_state(sp)
        assert state is not None
        assert state.ended_at == 0.0

    def test_campaign_ended_at_round_trips(self, tmp_path: Path) -> None:
        """ended_at persists through write/read cycle."""
        from autoskillit.fleet.state import _write_state

        sp = _state_path(tmp_path)
        write_initial_state(sp, "cid", "camp", "/m.yaml", _make_dispatches("d1"))
        state = read_state(sp)
        assert state is not None
        state.ended_at = 1234567890.0
        _write_state(sp, state)
        reloaded = read_state(sp)
        assert reloaded is not None
        assert reloaded.ended_at == 1234567890.0

    def test_campaign_ended_at_set_when_all_dispatches_terminal(self, tmp_path: Path) -> None:
        """ended_at is auto-set when append_dispatch_record makes all dispatches terminal."""
        sp = _state_path(tmp_path)
        write_initial_state(sp, "cid", "camp", "/m.yaml", _make_dispatches("d1"))
        append_dispatch_record(sp, DispatchRecord(name="d1", status=DispatchStatus.SUCCESS))
        state = read_state(sp)
        assert state is not None
        assert state.ended_at > 0.0

    def test_campaign_ended_at_not_set_when_pending_remains(self, tmp_path: Path) -> None:
        """ended_at stays 0.0 when non-terminal dispatches remain."""
        sp = _state_path(tmp_path)
        write_initial_state(sp, "cid", "camp", "/m.yaml", _make_dispatches("d1", "d2"))
        append_dispatch_record(sp, DispatchRecord(name="d1", status=DispatchStatus.SUCCESS))
        state = read_state(sp)
        assert state is not None
        assert state.ended_at == 0.0


class TestRecipeSnapshot:
    def test_recipe_snapshot_defaults_to_empty(self, tmp_path: Path) -> None:
        """recipe_snapshot defaults to {} on state files without it."""
        sp = _state_path(tmp_path)
        write_initial_state(sp, "cid", "camp", "/m.yaml", _make_dispatches("d1"))
        state = read_state(sp)
        assert state is not None
        assert state.recipe_snapshot == {}

    def test_recipe_snapshot_round_trips(self, tmp_path: Path) -> None:
        """recipe_snapshot is written and read back faithfully."""
        sp = _state_path(tmp_path)
        snapshot = {
            "recipe_name": "fix-bugs",
            "content_hash": "sha256:abc123",
            "effective_ingredients": {"task": "fix issue #42"},
        }
        write_initial_state(
            sp, "cid", "camp", "/m.yaml", _make_dispatches("d1"), recipe_snapshot=snapshot
        )
        state = read_state(sp)
        assert state is not None
        assert state.recipe_snapshot == snapshot


class TestV4BackwardCompat:
    def test_v4_state_file_loads_with_defaults(self, tmp_path: Path) -> None:
        """State file without new fields (v4) loads with correct defaults."""
        sp = _state_path(tmp_path)
        sp.parent.mkdir(parents=True, exist_ok=True)
        v4_data = {
            "schema_version": 4,
            "campaign_id": "cid",
            "campaign_name": "camp",
            "manifest_path": "/m.yaml",
            "started_at": 1000.0,
            "dispatches": [{"name": "d1", "status": "pending"}],
            "captured_values": {},
            "orchestrator_session_id": "",
        }
        sp.write_text(json.dumps(v4_data))
        state = read_state(sp)
        assert state is not None
        assert state.ended_at == 0.0
        assert state.recipe_snapshot == {}
        assert state.dispatches[0].attempt_history == []


class TestDispatchStatusStateMachineInvariants:
    """Structural immunity tests for the DispatchStatus state machine.

    These tests verify that _ALLOWED_TRANSITIONS and TERMINAL_DISPATCH_STATUSES
    are consistent by construction, preventing the class of bugs where a status
    has empty transitions but is not marked terminal (causing silent campaign stalls).
    """

    def test_every_dispatch_status_in_allowed_transitions(self) -> None:
        """Every DispatchStatus member must appear as a key in _ALLOWED_TRANSITIONS."""
        from autoskillit.fleet.state_types import _ALLOWED_TRANSITIONS

        for status in DispatchStatus:
            assert status in _ALLOWED_TRANSITIONS, (
                f"DispatchStatus.{status.name} missing from _ALLOWED_TRANSITIONS"
            )

    def test_nonterminal_status_has_outgoing_transitions(self) -> None:
        """Every non-terminal status must have at least one outgoing transition."""
        from autoskillit.fleet.state_types import (
            _ALLOWED_TRANSITIONS,
            TERMINAL_DISPATCH_STATUSES,
        )

        for status in DispatchStatus:
            if status not in TERMINAL_DISPATCH_STATUSES:
                assert len(_ALLOWED_TRANSITIONS[status]) > 0, (
                    f"Non-terminal status {status!r} has no outgoing transitions"
                )

    def test_terminal_set_matches_empty_transitions(self) -> None:
        """TERMINAL_DISPATCH_STATUSES must equal the set of statuses with empty transitions."""
        from autoskillit.fleet.state_types import (
            _ALLOWED_TRANSITIONS,
            TERMINAL_DISPATCH_STATUSES,
        )

        empty_transition_statuses = frozenset(s for s, t in _ALLOWED_TRANSITIONS.items() if not t)
        assert TERMINAL_DISPATCH_STATUSES == empty_transition_statuses, (
            f"TERMINAL_DISPATCH_STATUSES {TERMINAL_DISPATCH_STATUSES!r} != "
            f"derived empty-transition set {empty_transition_statuses!r}"
        )


class TestAllInterruptedCampaignDoesNotSilentlyComplete:
    """A campaign where every dispatch is INTERRUPTED must halt or retry, not silently complete."""

    def test_all_interrupted_continue_on_failure_false_halts(self, tmp_path: Path) -> None:
        """All-INTERRUPTED + continue_on_failure=False: empty name and FLEET_HALTED_SENTINEL."""
        sp = _state_path(tmp_path)
        write_initial_state(sp, "cid", "camp", "/m.yaml", _make_dispatches("A"))
        from autoskillit.fleet import upsert_dispatch_record_by_name

        upsert_dispatch_record_by_name(
            sp, DispatchRecord(name="A", status=DispatchStatus.INTERRUPTED)
        )

        decision = resume_campaign_from_state(sp, continue_on_failure=False)

        assert decision is not None
        assert decision.next_dispatch_name == ""
        assert decision.completed_dispatches_block == FLEET_HALTED_SENTINEL

    def test_all_interrupted_continue_on_failure_true_resets_to_pending(
        self, tmp_path: Path
    ) -> None:
        """continue_on_failure=True resets INTERRUPTED dispatch to PENDING."""
        sp = _state_path(tmp_path)
        write_initial_state(sp, "cid", "camp", "/m.yaml", _make_dispatches("A"))
        from autoskillit.fleet import upsert_dispatch_record_by_name

        upsert_dispatch_record_by_name(
            sp,
            DispatchRecord(name="A", status=DispatchStatus.INTERRUPTED, retry_reason="stale"),
        )

        decision = resume_campaign_from_state(sp, continue_on_failure=True, reset_on_retry=True)

        state = read_state(sp)
        assert state is not None
        assert state.dispatches[0].status == DispatchStatus.PENDING
        assert decision is not None
        assert decision.next_dispatch_name == "A"


class TestAllRefusedCampaignDoesNotSilentlyComplete:
    """A campaign where every dispatch is REFUSED must halt or retry, not silently complete."""

    def test_all_refused_continue_on_failure_false_halts(self, tmp_path: Path) -> None:
        """All-REFUSED + continue_on_failure=False: empty name and FLEET_HALTED_SENTINEL."""
        sp = _state_path(tmp_path)
        write_initial_state(sp, "cid", "camp", "/m.yaml", _make_dispatches("A"))
        from autoskillit.fleet import upsert_dispatch_record_by_name

        upsert_dispatch_record_by_name(sp, DispatchRecord(name="A", status=DispatchStatus.REFUSED))

        decision = resume_campaign_from_state(sp, continue_on_failure=False)

        assert decision is not None
        assert decision.next_dispatch_name == ""
        assert decision.completed_dispatches_block == FLEET_HALTED_SENTINEL

    def test_all_refused_continue_on_failure_true_resets_to_pending(self, tmp_path: Path) -> None:
        """resume_campaign_from_state with continue_on_failure=True resets REFUSED to PENDING."""
        sp = _state_path(tmp_path)
        write_initial_state(sp, "cid", "camp", "/m.yaml", _make_dispatches("A"))
        from autoskillit.fleet import upsert_dispatch_record_by_name

        upsert_dispatch_record_by_name(
            sp,
            DispatchRecord(name="A", status=DispatchStatus.REFUSED),
        )

        decision = resume_campaign_from_state(sp, continue_on_failure=True, reset_on_retry=True)

        state = read_state(sp)
        assert state is not None
        assert state.dispatches[0].status == DispatchStatus.PENDING
        assert decision is not None
        assert decision.next_dispatch_name == "A"


class TestRetryReasonNotStaleAfterResumableRedispatch:
    """retry_reason must be cleared when a RESUMABLE dispatch is redispatched to RUNNING."""

    def test_retry_reason_cleared_on_resumable_to_running(self, tmp_path: Path) -> None:
        """mark_dispatch_running clears retry_reason when re-dispatching a RESUMABLE dispatch."""
        from autoskillit.fleet import upsert_dispatch_record_by_name

        sp = _state_path(tmp_path)
        sidecar = tmp_path / "sidecar.jsonl"
        sidecar.write_text("")
        write_initial_state(sp, "cid", "camp", "/m.yaml", _make_dispatches("A"))
        mark_dispatch_running(sp, "A", dispatch_id="d1", dispatched_pid=42)
        mark_dispatch_resumable(sp, "A", sidecar_path=str(sidecar))
        upsert_dispatch_record_by_name(
            sp,
            DispatchRecord(
                name="A",
                status=DispatchStatus.RESUMABLE,
                retry_reason="idle_stall",
                infra_exit_category="something",
                sidecar_path=str(sidecar),
            ),
        )

        mark_dispatch_running(sp, "A", dispatch_id="d2", dispatched_pid=43)

        state = read_state(sp)
        assert state is not None
        a = next(d for d in state.dispatches if d.name == "A")
        assert a.retry_reason == "", "retry_reason was not cleared on RESUMABLE→RUNNING transition"
        assert a.infra_exit_category == "", (
            "infra_exit_category was not cleared on RESUMABLE→RUNNING transition"
        )


class TestL3GateBlocksOnInterruptedDispatch:
    """The L3 dispatch gate must block on INTERRUPTED and REFUSED dispatches."""

    def test_has_blocking_dispatch_detects_interrupted(self, tmp_path: Path) -> None:
        """A campaign with an INTERRUPTED dispatch must be detected by has_blocking_dispatch."""
        from autoskillit.fleet import has_blocking_dispatch, upsert_dispatch_record_by_name

        sp = _state_path(tmp_path)
        write_initial_state(sp, "cid", "camp", "/m.yaml", _make_dispatches("A"))
        upsert_dispatch_record_by_name(
            sp, DispatchRecord(name="A", status=DispatchStatus.INTERRUPTED)
        )

        assert has_blocking_dispatch(sp) is True

    def test_has_blocking_dispatch_detects_refused(self, tmp_path: Path) -> None:
        """A campaign with a REFUSED dispatch must be detected by has_blocking_dispatch."""
        from autoskillit.fleet import has_blocking_dispatch, upsert_dispatch_record_by_name

        sp = _state_path(tmp_path)
        write_initial_state(sp, "cid", "camp", "/m.yaml", _make_dispatches("A"))
        upsert_dispatch_record_by_name(sp, DispatchRecord(name="A", status=DispatchStatus.REFUSED))

        assert has_blocking_dispatch(sp) is True

    def test_has_blocking_dispatch_allows_success_only(self, tmp_path: Path) -> None:
        """A campaign with only SUCCESS dispatches must not be blocked."""
        from autoskillit.fleet import has_blocking_dispatch

        sp = _state_path(tmp_path)
        write_initial_state(sp, "cid", "camp", "/m.yaml", _make_dispatches("A", "B"))
        append_dispatch_record(sp, DispatchRecord(name="A", status=DispatchStatus.SUCCESS))
        append_dispatch_record(sp, DispatchRecord(name="B", status=DispatchStatus.SUCCESS))

        assert has_blocking_dispatch(sp) is False


class TestContinueOnFailureDoesNotResetFailureDispatches:
    """FAILURE dispatches must NOT be reset when continue_on_failure=True.

    Design intent: continue_on_failure skips halting on FAILURE but deliberately
    does not retry FAILURE dispatches — only INTERRUPTED/REFUSED are reset.
    Confirmed by commit b1476f2d.
    """

    def test_failure_not_reset_under_continue_on_failure(self, tmp_path: Path) -> None:
        """resume_campaign_from_state with continue_on_failure=True must not reset FAILURE."""
        from autoskillit.fleet import upsert_dispatch_record_by_name

        sp = _state_path(tmp_path)
        write_initial_state(sp, "cid", "camp", "/m.yaml", _make_dispatches("A", "B"))
        upsert_dispatch_record_by_name(
            sp,
            DispatchRecord(name="A", status=DispatchStatus.FAILURE, reason="task-failed"),
        )

        resume_campaign_from_state(sp, continue_on_failure=True, reset_on_retry=True)

        state = read_state(sp)
        assert state is not None
        a = next(d for d in state.dispatches if d.name == "A")
        assert a.status == DispatchStatus.FAILURE, (
            "FAILURE dispatch must not be reset to PENDING under continue_on_failure=True"
        )

    def test_interrupted_still_reset_under_continue_on_failure(self, tmp_path: Path) -> None:
        """INTERRUPTED dispatches ARE reset when continue_on_failure=True (not FAILURE)."""
        from autoskillit.fleet import upsert_dispatch_record_by_name

        sp = _state_path(tmp_path)
        write_initial_state(sp, "cid", "camp", "/m.yaml", _make_dispatches("A", "B"))
        upsert_dispatch_record_by_name(
            sp, DispatchRecord(name="A", status=DispatchStatus.INTERRUPTED)
        )

        resume_campaign_from_state(sp, continue_on_failure=True, reset_on_retry=True)

        state = read_state(sp)
        assert state is not None
        a = next(d for d in state.dispatches if d.name == "A")
        assert a.status == DispatchStatus.PENDING


class TestUpsertCannotOverwriteFailureWithSuccess:
    """upsert_dispatch_record_by_name must reject overwriting FAILURE with SUCCESS."""

    def test_upsert_cannot_overwrite_failure_with_success(self, tmp_path: Path) -> None:
        """Overwriting a FAILURE dispatch record with SUCCESS must raise ValueError."""
        from autoskillit.fleet import upsert_dispatch_record_by_name

        sp = _state_path(tmp_path)
        write_initial_state(sp, "cid", "camp", "/m.yaml", _make_dispatches("run-implement"))

        # Write an initial FAILURE record
        upsert_dispatch_record_by_name(
            sp,
            DispatchRecord(
                name="run-implement", status=DispatchStatus.FAILURE, reason="task-failed"
            ),
        )

        # Attempting to overwrite with SUCCESS must raise
        with pytest.raises(ValueError) as exc_info:
            upsert_dispatch_record_by_name(
                sp,
                DispatchRecord(
                    name="run-implement", status=DispatchStatus.SUCCESS, reason="all good"
                ),
            )
        assert "FAILURE" in str(exc_info.value)
        assert "run-implement" in str(exc_info.value)


class TestUpsertFailureToFailureSnapshotsPrior:
    """FAILURE-to-FAILURE overwrites must snapshot prior diagnostics to attempt_history."""

    def test_upsert_failure_to_failure_snapshots_prior(self, tmp_path: Path) -> None:
        """FAILURE record with non-empty reason is snapshotted before overwrite."""
        from autoskillit.fleet import upsert_dispatch_record_by_name

        sp = _state_path(tmp_path)
        write_initial_state(sp, "cid", "camp", "/m.yaml", [DispatchRecord(name="run-implement")])

        upsert_dispatch_record_by_name(
            sp,
            DispatchRecord(
                name="run-implement",
                status=DispatchStatus.FAILURE,
                reason="fleet_l3_parse_failed",
                retry_reason="context_exhausted",
            ),
        )

        upsert_dispatch_record_by_name(
            sp,
            DispatchRecord(
                name="run-implement",
                status=DispatchStatus.FAILURE,
                reason="fleet_l3_no_result_block",
            ),
        )

        state = read_state(sp)
        assert state is not None
        disp = next(d for d in state.dispatches if d.name == "run-implement")
        assert len(disp.attempt_history) == 1
        snapshot = disp.attempt_history[0]
        assert snapshot["reason"] == "fleet_l3_parse_failed"
        assert snapshot["retry_reason"] == "context_exhausted"
        assert disp.status == DispatchStatus.FAILURE
        assert disp.reason == "fleet_l3_no_result_block"

    def test_upsert_failure_to_failure_no_snapshot_when_reason_empty(self, tmp_path: Path) -> None:
        """No snapshot is taken when existing record has empty reason."""
        from autoskillit.fleet import upsert_dispatch_record_by_name

        sp = _state_path(tmp_path)
        write_initial_state(sp, "cid", "camp", "/m.yaml", [DispatchRecord(name="run-implement")])

        upsert_dispatch_record_by_name(
            sp,
            DispatchRecord(
                name="run-implement",
                status=DispatchStatus.FAILURE,
                reason="",
            ),
        )

        upsert_dispatch_record_by_name(
            sp,
            DispatchRecord(
                name="run-implement",
                status=DispatchStatus.FAILURE,
                reason="fleet_l3_no_result_block",
            ),
        )

        state = read_state(sp)
        assert state is not None
        disp = next(d for d in state.dispatches if d.name == "run-implement")
        assert len(disp.attempt_history) == 0

    def test_upsert_failure_to_failure_preserves_existing_attempt_history(
        self, tmp_path: Path
    ) -> None:
        """Pre-existing attempt_history is preserved when new snapshot is prepended."""
        from autoskillit.fleet import upsert_dispatch_record_by_name

        sp = _state_path(tmp_path)
        write_initial_state(sp, "cid", "camp", "/m.yaml", [DispatchRecord(name="run-implement")])

        first_record = DispatchRecord(
            name="run-implement",
            status=DispatchStatus.FAILURE,
            reason="first_failure",
            attempt_history=[],
        )
        upsert_dispatch_record_by_name(sp, first_record)

        second_record = DispatchRecord(
            name="run-implement",
            status=DispatchStatus.FAILURE,
            reason="second_failure",
            attempt_history=[{"dispatch_id": "prior-attempt", "status": "failure"}],
        )
        upsert_dispatch_record_by_name(sp, second_record)

        state = read_state(sp)
        assert state is not None
        disp = next(d for d in state.dispatches if d.name == "run-implement")
        assert len(disp.attempt_history) == 2
        assert disp.attempt_history[0]["reason"] == "first_failure"
        assert disp.attempt_history[1]["dispatch_id"] == "prior-attempt"
