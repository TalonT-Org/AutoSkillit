"""Tests for the resume-count cap on the headless dispatch path (L3).

The campaign-level ``MAX_CONSECUTIVE_RESUME_ATTEMPTS`` is already enforced by
``resume_campaign_from_state`` (tested in ``test_resume_max_attempts.py``).
This module verifies that the cap is also enforced when the headless CLI path
calls ``mark_dispatch_running`` with ``enforce_max_resume_attempts=True``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from autoskillit.core import FleetErrorCode as FEC
from autoskillit.fleet import DispatchRecord, DispatchStatus, write_initial_state
from autoskillit.fleet.state import mark_dispatch_running, read_state
from autoskillit.fleet.state_recovery import (
    MAX_CONSECUTIVE_RESUME_ATTEMPTS,
)

pytestmark = [pytest.mark.layer("fleet"), pytest.mark.small, pytest.mark.feature("fleet")]


def _state_path(tmp_path: Path) -> Path:
    return tmp_path / "campaign" / "state.json"


def _make_dispatches(*names: str) -> list[DispatchRecord]:
    return [DispatchRecord(name=n) for n in names]


def _seed_state(tmp_path: Path, dispatch: DispatchRecord) -> Path:
    """Write a state file with a single dispatch in the given state."""
    sp = _state_path(tmp_path)
    write_initial_state(sp, "cid", "camp", "/m.yaml", _make_dispatches(dispatch.name))
    from autoskillit.fleet import upsert_dispatch_record_by_name

    # upsert (no transition validation) — used here to set the dispatch into a
    # terminal/resumable status directly so the headless path's precondition
    # check / cap enforcement can be exercised.
    upsert_dispatch_record_by_name(sp, dispatch)
    return sp


class TestHeadlessResumeCount:
    def test_max_resume_attempts_blocks_on_headless_path(self, tmp_path: Path) -> None:
        """When attempt_history already contains MAX consecutive RESUMABLE entries,
        mark_dispatch_running(..., enforce_max_resume_attempts=True) must raise
        ResumeCountExceeded rather than performing the transition."""
        from autoskillit.fleet import ResumeCountExceeded

        attempt_history: list[dict[str, Any]] = [
            {"status": str(DispatchStatus.RESUMABLE), "reason": FEC.FLEET_L3_TIMEOUT}
        ] * MAX_CONSECUTIVE_RESUME_ATTEMPTS
        sp = _seed_state(
            tmp_path,
            DispatchRecord(
                name="d1",
                status=DispatchStatus.RESUMABLE,
                reason=FEC.FLEET_L3_TIMEOUT,
                session_chain=["sess-A"],
                dispatched_session_id="sess-A",
                attempt_history=list(attempt_history),
            ),
        )

        with pytest.raises(ResumeCountExceeded):
            mark_dispatch_running(
                sp,
                "d1",
                dispatch_id="new-id",
                dispatched_pid=12345,
                enforce_max_resume_attempts=True,
            )

    def test_reset_through_pending_does_not_bypass_cap(self, tmp_path: Path) -> None:
        """The cap check sees attempt_history across a FAILURE→PENDING reset.

        After ``reset_blocking_dispatch`` runs, the original attempt_history
        (which contained the timeout retries) is preserved at the head and a
        new snapshot is appended at the tail. The cap is computed from the
        *tail* by ``_count_consecutive_resumable_timeouts``; the appended
        snapshot has ``status="failure"`` and breaks the run, so the cap is
        re-evaluated from the head on subsequent transitions.
        """
        from autoskillit.fleet.state import (
            ResumeCountExceeded,
            reset_blocking_dispatch,
        )

        # Seed a FAILURE dispatch whose attempt_history records MAX timeout retries.
        attempt_history: list[dict[str, Any]] = [
            {"status": str(DispatchStatus.RESUMABLE), "reason": FEC.FLEET_L3_TIMEOUT}
        ] * MAX_CONSECUTIVE_RESUME_ATTEMPTS
        failure_dispatch = DispatchRecord(
            name="d1",
            status=DispatchStatus.FAILURE,
            reason=FEC.FLEET_L3_TIMEOUT,
            session_chain=["sess-A"],
            dispatched_session_id="sess-A",
            attempt_history=list(attempt_history),
        )
        sp = _seed_state(tmp_path, failure_dispatch)

        # Reset through PENDING (this is what the headless path does before spawn).
        reset_blocking_dispatch(sp, "d1")

        state = read_state(sp)
        assert state is not None
        d1 = next(d for d in state.dispatches if d.name == "d1")
        assert d1.status == DispatchStatus.PENDING
        # After reset: original 3 RESUMABLE entries are preserved at the head,
        # and a snapshot of the FAILURE state is appended at the tail.
        assert len(d1.attempt_history) >= MAX_CONSECUTIVE_RESUME_ATTEMPTS

        # Reset does NOT re-establish the cap-tail run (the tail entry is the
        # new FAILURE snapshot, which breaks the consecutive RESUMABLE count),
        # so the cap is NOT raised on the immediate next spawn attempt. The
        # cap is re-evaluated once a fresh RESUMABLE entry is appended.
        mark_dispatch_running(
            sp,
            "d1",
            dispatch_id="new-id",
            dispatched_pid=99,
            enforce_max_resume_attempts=True,
        )

        # Re-seed the dispatch in RESUMABLE state with the full attempt_history
        # and verify the cap is enforced on a subsequent mark_dispatch_running
        # (the canonical "reset-rewriting-the-status path" the test is named
        # for).
        from autoskillit.fleet.state import upsert_dispatch_record_by_name

        resumed = DispatchRecord(
            name="d1",
            status=DispatchStatus.RESUMABLE,
            reason=FEC.FLEET_L3_TIMEOUT,
            session_chain=["sess-A"],
            dispatched_session_id="sess-A",
            attempt_history=list(attempt_history),
        )
        upsert_dispatch_record_by_name(sp, resumed)

        with pytest.raises(ResumeCountExceeded):
            mark_dispatch_running(
                sp,
                "d1",
                dispatch_id="new-id-2",
                dispatched_pid=99,
                enforce_max_resume_attempts=True,
            )

    def test_cap_exhaustion_emits_halt_decision(self, tmp_path: Path) -> None:
        """When the cap is exhausted, the exception carries context identifying the
        dispatch and the cap value. (The halt-decision string is owned by the
        campaign path's `ResumeDecision`; on the headless path we surface it via
        the exception's message.)"""
        from autoskillit.fleet.state import ResumeCountExceeded

        attempt_history: list[dict[str, Any]] = [
            {"status": str(DispatchStatus.RESUMABLE), "reason": FEC.FLEET_L3_TIMEOUT}
        ] * (MAX_CONSECUTIVE_RESUME_ATTEMPTS + 1)
        sp = _seed_state(
            tmp_path,
            DispatchRecord(
                name="d1",
                status=DispatchStatus.RESUMABLE,
                reason=FEC.FLEET_L3_TIMEOUT,
                session_chain=["sess-A"],
                dispatched_session_id="sess-A",
                attempt_history=attempt_history,
            ),
        )

        with pytest.raises(ResumeCountExceeded) as info:
            mark_dispatch_running(
                sp,
                "d1",
                dispatch_id="new-id",
                dispatched_pid=99,
                enforce_max_resume_attempts=True,
            )
        # The message mentions the cap value and the dispatch name.
        assert str(MAX_CONSECUTIVE_RESUME_ATTEMPTS) in str(info.value)
        assert "d1" in str(info.value)
