"""Tests for retry of failed campaign dispatches (Group J extension)."""

from __future__ import annotations

import dataclasses
from pathlib import Path
from typing import Any

import pytest

from autoskillit.fleet import (
    FLEET_HALTED_SENTINEL,
    DispatchRecord,
    DispatchStatus,
    append_dispatch_record,
    read_state,
    reset_failed_dispatch,
    resume_campaign_from_state,
    write_initial_state,
)
from autoskillit.fleet.state import _clear_dispatch_for_retry
from autoskillit.fleet.state_types import _validate_transition

pytestmark = [pytest.mark.layer("fleet"), pytest.mark.small, pytest.mark.feature("fleet")]


def _make_dispatches(*names: str) -> list[DispatchRecord]:
    return [DispatchRecord(name=n) for n in names]


def _state_path(tmp_path: Path) -> Path:
    return tmp_path / "campaign" / "state.json"


# --- Transition validity ---


class TestFailureToPendingTransition:
    def test_failure_to_pending_is_valid(self):
        """_validate_transition accepts FAILURE → PENDING."""
        _validate_transition(DispatchStatus.FAILURE, DispatchStatus.PENDING, "d1")

    def test_failure_to_running_still_invalid(self):
        """FAILURE → RUNNING is still rejected (only PENDING is allowed)."""
        with pytest.raises(ValueError, match="Invalid transition"):
            _validate_transition(DispatchStatus.FAILURE, DispatchStatus.RUNNING, "d1")


# --- reset_failed_dispatch ---


class TestResetFailedDispatch:
    def test_resets_failure_to_pending(self, tmp_path: Path):
        """FAILURE dispatch is reset to PENDING with cleared metadata."""
        sp = _state_path(tmp_path)
        write_initial_state(sp, "cid", "camp", "/m.yaml", _make_dispatches("d1", "d2"))
        append_dispatch_record(
            sp,
            DispatchRecord(
                name="d1",
                status=DispatchStatus.SUCCESS,
            ),
        )
        append_dispatch_record(
            sp,
            DispatchRecord(
                name="d2",
                status=DispatchStatus.FAILURE,
                reason="task_failed",
                dispatch_id="old-dispatch-id",
                dispatched_session_id="old-session",
                dispatched_pid=12345,
                dispatched_starttime_ticks=999,
                dispatched_boot_id="old-boot",
                token_usage={"prompt_tokens": 100},
                started_at=1000.0,
                ended_at=2000.0,
                sidecar_path="/old/sidecar",
                kill_reason="stale",
                infra_exit_category="timeout",
            ),
        )

        result = reset_failed_dispatch(sp, "d2")

        assert result is True
        state = read_state(sp)
        assert state is not None
        d2 = next(d for d in state.dispatches if d.name == "d2")
        assert d2.status == DispatchStatus.PENDING
        assert d2.reason == ""
        assert d2.dispatch_id == ""
        assert d2.dispatched_session_id == ""
        assert d2.dispatched_session_log_dir == ""
        assert d2.dispatched_pid == 0
        assert d2.dispatched_starttime_ticks == 0
        assert d2.dispatched_boot_id == ""
        assert d2.token_usage == {}
        assert d2.started_at == 0.0
        assert d2.ended_at == 0.0
        assert d2.sidecar_path is None
        assert d2.kill_reason == ""
        assert d2.infra_exit_category == ""

    def test_returns_true_on_success(self, tmp_path: Path):
        """Returns True when the dispatch was actually reset."""
        sp = _state_path(tmp_path)
        write_initial_state(sp, "cid", "camp", "/m.yaml", _make_dispatches("d1"))
        append_dispatch_record(sp, DispatchRecord(name="d1", status=DispatchStatus.FAILURE))

        result = reset_failed_dispatch(sp, "d1")

        assert result is True

    def test_returns_false_for_non_failure_dispatch(self, tmp_path: Path):
        """Returns False when dispatch is PENDING/SUCCESS/etc."""
        sp = _state_path(tmp_path)
        write_initial_state(sp, "cid", "camp", "/m.yaml", _make_dispatches("d1"))
        append_dispatch_record(sp, DispatchRecord(name="d1", status=DispatchStatus.SUCCESS))

        result = reset_failed_dispatch(sp, "d1")

        assert result is False

    def test_returns_false_for_unknown_dispatch_name(self, tmp_path: Path):
        """Returns False when dispatch_name not found in state."""
        sp = _state_path(tmp_path)
        write_initial_state(sp, "cid", "camp", "/m.yaml", _make_dispatches("d1"))
        append_dispatch_record(sp, DispatchRecord(name="d1", status=DispatchStatus.FAILURE))

        result = reset_failed_dispatch(sp, "nonexistent")

        assert result is False

    def test_returns_false_for_missing_state_file(self, tmp_path: Path):
        """Returns False when state file does not exist (fail-safe)."""
        sp = tmp_path / "nonexistent" / "state.json"

        result = reset_failed_dispatch(sp, "d1")

        assert result is False

    def test_preserves_other_dispatches(self, tmp_path: Path):
        """Resetting d2 does not affect d1's state."""
        sp = _state_path(tmp_path)
        write_initial_state(sp, "cid", "camp", "/m.yaml", _make_dispatches("d1", "d2"))
        append_dispatch_record(
            sp,
            DispatchRecord(
                name="d1",
                status=DispatchStatus.SUCCESS,
                dispatch_id="d1-dispatch",
                reason="done",
            ),
        )
        append_dispatch_record(
            sp,
            DispatchRecord(
                name="d2",
                status=DispatchStatus.FAILURE,
                reason="task_failed",
            ),
        )

        reset_failed_dispatch(sp, "d2")

        state = read_state(sp)
        assert state is not None
        d1 = next(d for d in state.dispatches if d.name == "d1")
        assert d1.status == DispatchStatus.SUCCESS
        assert d1.dispatch_id == "d1-dispatch"
        assert d1.reason == "done"


# --- resume_campaign_from_state with reset_on_retry ---


class TestResumeResetOnRetry:
    def test_reset_on_retry_resets_failure_and_selects_as_next(self, tmp_path: Path):
        """reset_on_retry=True resets FAILURE dispatch to PENDING, selects as next."""
        sp = _state_path(tmp_path)
        write_initial_state(sp, "cid", "camp", "/m.yaml", _make_dispatches("d1", "d2", "d3"))
        append_dispatch_record(sp, DispatchRecord(name="d1", status=DispatchStatus.SUCCESS))
        append_dispatch_record(
            sp,
            DispatchRecord(
                name="d2",
                status=DispatchStatus.FAILURE,
                dispatch_id="d2-uuid",
                dispatched_session_id="d2-sess",
            ),
        )

        decision = resume_campaign_from_state(sp, continue_on_failure=False, reset_on_retry=True)

        assert decision is not None
        assert decision.next_dispatch_name == "d2"
        assert decision.completed_dispatches_block != FLEET_HALTED_SENTINEL
        state = read_state(sp)
        assert state is not None
        d2 = next(d for d in state.dispatches if d.name == "d2")
        assert d2.status == DispatchStatus.PENDING
        assert d2.dispatch_id == ""
        assert d2.dispatched_session_id == ""
        d3 = next(d for d in state.dispatches if d.name == "d3")
        assert d3.status == DispatchStatus.PENDING
        assert d3.dispatch_id == ""

    def test_reset_on_retry_false_still_halts(self, tmp_path: Path):
        """reset_on_retry=False preserves existing halt behavior."""
        sp = _state_path(tmp_path)
        write_initial_state(sp, "cid", "camp", "/m.yaml", _make_dispatches("d1", "d2"))
        append_dispatch_record(sp, DispatchRecord(name="d1", status=DispatchStatus.SUCCESS))
        append_dispatch_record(sp, DispatchRecord(name="d2", status=DispatchStatus.FAILURE))

        decision = resume_campaign_from_state(sp, continue_on_failure=False, reset_on_retry=False)

        assert decision is not None
        assert decision.completed_dispatches_block == FLEET_HALTED_SENTINEL

    def test_reset_on_retry_with_continue_on_failure_true_is_noop(self, tmp_path: Path):
        """When continue_on_failure=True, reset_on_retry has no effect on FAILURE dispatches."""
        sp = _state_path(tmp_path)
        write_initial_state(sp, "cid", "camp", "/m.yaml", _make_dispatches("d1", "d2", "d3"))
        append_dispatch_record(sp, DispatchRecord(name="d1", status=DispatchStatus.SUCCESS))
        append_dispatch_record(sp, DispatchRecord(name="d2", status=DispatchStatus.FAILURE))

        decision = resume_campaign_from_state(sp, continue_on_failure=True, reset_on_retry=True)

        assert decision is not None
        assert decision.next_dispatch_name == "d3"
        assert decision.completed_dispatches_block != FLEET_HALTED_SENTINEL
        state = read_state(sp)
        assert state is not None
        d2 = next(d for d in state.dispatches if d.name == "d2")
        assert d2.status == DispatchStatus.FAILURE

    def test_reset_on_retry_clears_dispatch_from_completed_block(self, tmp_path: Path):
        """Reset dispatch does NOT appear in completed_dispatches_block."""
        sp = _state_path(tmp_path)
        write_initial_state(sp, "cid", "camp", "/m.yaml", _make_dispatches("d1", "d2"))
        append_dispatch_record(sp, DispatchRecord(name="d1", status=DispatchStatus.SUCCESS))
        append_dispatch_record(sp, DispatchRecord(name="d2", status=DispatchStatus.FAILURE))

        decision = resume_campaign_from_state(sp, continue_on_failure=False, reset_on_retry=True)

        assert decision is not None
        assert decision.completed_dispatches_block == "- d1: success"

    def test_stale_kill_reason_does_not_survive_retry(self, tmp_path: Path):
        """Stale kill_reason from prior failure must not leak through retry reset."""
        sp = _state_path(tmp_path)
        write_initial_state(sp, "cid", "camp", "/m.yaml", _make_dispatches("d1", "d2"))
        append_dispatch_record(sp, DispatchRecord(name="d1", status=DispatchStatus.SUCCESS))
        append_dispatch_record(
            sp,
            DispatchRecord(
                name="d2",
                status=DispatchStatus.FAILURE,
                kill_reason="stale",
                infra_exit_category="timeout",
            ),
        )

        resume_campaign_from_state(sp, continue_on_failure=False, reset_on_retry=True)

        state = read_state(sp)
        assert state is not None
        d2 = next(d for d in state.dispatches if d.name == "d2")
        assert d2.kill_reason == ""
        assert d2.infra_exit_category == ""


# --- structural guard: field coverage ---


class TestClearDispatchFieldCoverage:
    """Structural guard: _clear_dispatch_for_retry must reset all execution-metadata fields."""

    _IDENTITY_FIELDS = frozenset({"name", "campaign_id", "caller_session_id"})

    def test_clear_covers_all_execution_metadata_fields(self):
        """Every non-identity field must be reset to its default by _clear_dispatch_for_retry."""
        dirty_values: dict[str, Any] = {
            "name": "test",
            "status": DispatchStatus.FAILURE,
            "dispatch_id": "dirty-id",
            "campaign_id": "cid",
            "caller_session_id": "csid",
            "dispatched_session_id": "dirty-sess",
            "dispatched_session_log_dir": "/dirty/log",
            "dispatched_pid": 99999,
            "dispatched_starttime_ticks": 12345,
            "dispatched_boot_id": "dirty-boot",
            "reason": "dirty-reason",
            "kill_reason": "stale",
            "infra_exit_category": "timeout",
            "token_usage": {"prompt_tokens": 500},
            "started_at": 1000.0,
            "ended_at": 2000.0,
            "sidecar_path": "/dirty/sidecar",
        }
        d = DispatchRecord(**dirty_values)
        _clear_dispatch_for_retry(d)

        for f in dataclasses.fields(d):
            if f.name in self._IDENTITY_FIELDS:
                continue
            default = (
                f.default_factory() if f.default_factory is not dataclasses.MISSING else f.default
            )
            assert getattr(d, f.name) == default, (
                f"_clear_dispatch_for_retry did not reset {f.name!r} to its default "
                f"({default!r}); got {getattr(d, f.name)!r}"
            )
