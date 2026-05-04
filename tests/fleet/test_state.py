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
    crash_recover_dispatch,
    has_failed_dispatch,
    mark_dispatch_resumable,
    mark_dispatch_running,
    read_all_campaign_captures,
    read_state,
    resume_campaign_from_state,
    write_captured_values,
    write_initial_state,
)

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
        assert state.schema_version == 4
        assert state.campaign_id == "cid-1"
        assert state.campaign_name == "my-campaign"
        assert state.manifest_path == "/m.yaml"
        assert len(state.dispatches) == 3
        for d in state.dispatches:
            assert d.status == DispatchStatus.PENDING


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
                mark_dispatch_running(sp, "a", dispatch_id="d1", l3_pid=42)

        assert sp.read_text(encoding="utf-8") == original

        # Retry succeeds
        mark_dispatch_running(sp, "a", dispatch_id="d1", l3_pid=42)
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
        mark_dispatch_running(sp, "B", dispatch_id="d-b", l3_pid=99)

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
            mark_dispatch_running(sp, "a", dispatch_id="d1", l3_pid=42)

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
                mark_dispatch_running(sp, "a", dispatch_id="d1", l3_pid=42)

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
    def test_read_v2_state_file_defaults_captured_values(self, tmp_path: Path) -> None:
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
        assert state is not None
        assert state.captured_values == {}


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
            l3_pid=12345,
            l3_boot_id="abc",
            l3_starttime_ticks=999,
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
            l3_pid=0,
            l3_boot_id="",
            l3_starttime_ticks=0,
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
            l3_pid=0,
            l3_boot_id="",
            l3_starttime_ticks=0,
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
            sp, "impl", dispatch_id="d1111", l3_pid=999, sidecar_path=str(sidecar_file)
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
        mark_dispatch_running(sp, "impl", dispatch_id="d1111", l3_pid=999)
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
            sp, "impl", dispatch_id="d1111", l3_pid=999, sidecar_path=str(sidecar_file)
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
            sp, "impl", dispatch_id="d1111", l3_pid=999, sidecar_path=str(sidecar_file)
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
        mark_dispatch_running(sp, "impl-1", dispatch_id="d1111", l3_pid=999)
        # Non-existent sidecar is intentional: test covers selection ordering only,
        # not the sidecar-existence branch in crash_recover_dispatch.
        mark_dispatch_resumable(sp, "impl-1", sidecar_path=str(sp.parent / "d1111_issues.jsonl"))

        decision = resume_campaign_from_state(sp, continue_on_failure=False)

        assert decision is not None
        assert decision.next_dispatch_name == "impl-1"
        assert decision.is_resumable is True

    def test_resume_decision_carries_l3_session_id(self, tmp_path: Path) -> None:
        sp = _state_path(tmp_path)
        write_initial_state(sp, "c1", "myCampaign", "manifest.yaml", _make_dispatches("impl-1"))
        mark_dispatch_running(sp, "impl-1", dispatch_id="d2222", l3_pid=888)
        append_dispatch_record(
            sp,
            DispatchRecord(
                name="impl-1",
                status=DispatchStatus.RESUMABLE,
                dispatch_id="d2222",
                l3_session_id="sess-xyz-test",
                sidecar_path=str(sp.parent / "d2222_issues.jsonl"),
            ),
        )

        decision = resume_campaign_from_state(sp, continue_on_failure=False)

        assert decision is not None
        assert decision.is_resumable is True
        assert decision.l3_session_id == "sess-xyz-test"


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
            mark_dispatch_running(sp, "impl", dispatch_id="d1", l3_pid=1)
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
        mark_dispatch_running(sp, "impl", dispatch_id="d1111", l3_pid=999)

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
            l3_pid=999,
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
        mark_dispatch_running(sp, "A", dispatch_id="d-a", l3_pid=99)
        append_dispatch_record(sp, DispatchRecord(name="A", status=DispatchStatus.SUCCESS))
        state = read_state(sp)
        assert state is not None
        latest = next(d for d in reversed(state.dispatches) if d.name == "A")
        assert latest.status == DispatchStatus.SUCCESS


class TestResumeShowsRefusedInBlock:
    def test_refused_dispatch_visible_next_is_b(self, tmp_path: Path) -> None:
        sp = _state_path(tmp_path)
        write_initial_state(sp, "cid", "camp", "/m.yaml", _make_dispatches("A", "B"))
        append_dispatch_record(sp, DispatchRecord(name="A", status=DispatchStatus.REFUSED))
        decision = resume_campaign_from_state(sp, continue_on_failure=True)
        assert decision is not None
        assert decision.next_dispatch_name == "B"
        assert "A" in decision.completed_dispatches_block
        assert "refused" in decision.completed_dispatches_block.lower()


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


class TestResumeIncludesInterruptedInBlock:
    def test_interrupted_dispatch_visible_in_completed_block(self, tmp_path: Path) -> None:
        sp = _state_path(tmp_path)
        write_initial_state(sp, "cid", "camp", "/m.yaml", _make_dispatches("A", "B", "C"))
        append_dispatch_record(sp, DispatchRecord(name="A", status=DispatchStatus.SUCCESS))
        mark_dispatch_running(sp, "B", dispatch_id="d-b", l3_pid=99)
        append_dispatch_record(sp, DispatchRecord(name="B", status=DispatchStatus.INTERRUPTED))
        decision = resume_campaign_from_state(sp, continue_on_failure=True)
        assert decision is not None
        assert "B" in decision.completed_dispatches_block
        assert "interrupted" in decision.completed_dispatches_block.lower()
        assert decision.next_dispatch_name == "C"


class TestResumeIncludesRunningAliveInBlock:
    def test_running_alive_dispatch_visible_in_completed_block(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        sp = _state_path(tmp_path)
        record_a = DispatchRecord(name="A", status=DispatchStatus.SUCCESS)
        record_b = DispatchRecord(
            name="B",
            status=DispatchStatus.RUNNING,
            l3_pid=12345,
            l3_boot_id="abc",
            l3_starttime_ticks=999,
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


class TestCrashRecoverDispatchSidecarVanished:
    def test_sidecar_oserror_yields_interrupted(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        sp = _state_path(tmp_path)
        write_initial_state(sp, "cid", "camp", "/m.yaml", _make_dispatches("impl"))
        sidecar = tmp_path / "sidecar.jsonl"
        sidecar.write_text('{"issue_url":"x","status":"completed"}\n', encoding="utf-8")
        mark_dispatch_running(sp, "impl", dispatch_id="d-1", l3_pid=99, sidecar_path=str(sidecar))

        record = read_state(sp).dispatches[0]

        original_read_text = Path.read_text

        def _oserror_read_text(self_path, *a, **kw):
            if str(self_path) == str(sidecar):
                raise OSError("TOCTOU race")
            return original_read_text(self_path, *a, **kw)

        monkeypatch.setattr(Path, "read_text", _oserror_read_text)

        result = crash_recover_dispatch(sp, record)
        assert result == DispatchStatus.INTERRUPTED
        assert read_state(sp).dispatches[0].status == DispatchStatus.INTERRUPTED


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
