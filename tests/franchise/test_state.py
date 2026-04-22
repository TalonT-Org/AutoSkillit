"""Tests for franchise state module (Group J)."""

from __future__ import annotations

import errno
import os
import threading
from pathlib import Path
from unittest.mock import patch

import pytest

from autoskillit.franchise import (
    DispatchRecord,
    DispatchStatus,
    append_dispatch_record,
    mark_dispatch_running,
    read_state,
    resume_campaign_from_state,
    write_initial_state,
)

pytestmark = [pytest.mark.layer("franchise"), pytest.mark.small]


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
        assert state.schema_version == 2
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
                mark_dispatch_running(sp, "a", dispatch_id="d1", l2_pid=42)

        assert sp.read_text(encoding="utf-8") == original

        # Retry succeeds
        mark_dispatch_running(sp, "a", dispatch_id="d1", l2_pid=42)
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
        mark_dispatch_running(sp, "B", dispatch_id="d-b", l2_pid=99)

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
        assert "franchise_halted_on_failure" in decision.completed_dispatches_block


class TestAtomicUnderConcurrentRead:
    def test_state_json_atomic_under_concurrent_read(self, tmp_path: Path) -> None:
        sp = _state_path(tmp_path)
        write_initial_state(sp, "cid", "camp", "/m.yaml", _make_dispatches("a"))

        barrier = threading.Barrier(2, timeout=5)
        errors: list[str] = []

        def writer():
            barrier.wait()
            mark_dispatch_running(sp, "a", dispatch_id="d1", l2_pid=42)

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
                mark_dispatch_running(sp, "a", dispatch_id="d1", l2_pid=42)

        assert sp.read_text(encoding="utf-8") == original


class TestReadStateRejectsCorrupted:
    def test_read_state_rejects_corrupted_json(self, tmp_path: Path) -> None:
        sp = _state_path(tmp_path)
        sp.parent.mkdir(parents=True, exist_ok=True)
        sp.write_bytes(b"not valid json {{{")

        result = read_state(sp)
        assert result is None
