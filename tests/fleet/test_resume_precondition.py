"""Tests for the centralized prepare_resume precondition chokepoint (Group J extension).

These tests verify that every resume entry point funnels through ``prepare_resume``
so the precondition gap that caused #4199 becomes structurally impossible.
"""

from __future__ import annotations

import inspect
from pathlib import Path

import pytest

from autoskillit.core import FleetErrorCode as FEC
from autoskillit.fleet import DispatchRecord, DispatchStatus, write_initial_state
from autoskillit.fleet.state import read_state
from autoskillit.fleet.state_recovery import ResumePreflight
from autoskillit.fleet.state_types import FLEET_HALTED_SENTINEL

pytestmark = [pytest.mark.layer("fleet"), pytest.mark.small, pytest.mark.feature("fleet")]


def _state_path(tmp_path: Path) -> Path:
    return tmp_path / "campaign" / "state.json"


def _make_dispatches(*names: str) -> list[DispatchRecord]:
    return [DispatchRecord(name=n) for n in names]


def _seed_state(tmp_path: Path, dispatches: list[DispatchRecord]) -> Path:
    """Seed a fresh state file containing the given dispatch records."""
    sp = _state_path(tmp_path)
    names = [d.name for d in dispatches]
    write_initial_state(sp, "cid", "camp", "/m.yaml", _make_dispatches(*names))
    for d in dispatches:
        # Use upsert_dispatch_record_by_name so we can override the freshly-seeded
        # PENDING record with arbitrary status (append_dispatch_record validates
        # transitions, but here we want to set terminal/resumable statuses
        # directly).
        from autoskillit.fleet import upsert_dispatch_record_by_name

        upsert_dispatch_record_by_name(sp, d)
    return sp


# ----------------------------------------------------------------------------
# Chokepoint unit tests — TestPrepareResume
# ----------------------------------------------------------------------------


class TestPrepareResume:
    """Direct unit tests for the prepare_resume chokepoint.

    These tests intentionally avoid importing prepare_resume by name at module
    top, because the symbol does not yet exist (this is a TDD plan). The
    tests will be skipped with a clear import error if the symbol is missing
    so the implementation step is the missing piece.
    """

    def _prepare_resume(self):
        from autoskillit.fleet.state_recovery import prepare_resume

        return prepare_resume

    def test_prepare_resume_short_circuits_on_success(self, tmp_path: Path) -> None:
        prepare_resume = self._prepare_resume()
        sp = _seed_state(
            tmp_path,
            [DispatchRecord(name="d1", status=DispatchStatus.SUCCESS)],
        )
        preflight = prepare_resume(sp, "d1")
        assert preflight is not None
        assert preflight.short_circuit is not None
        assert preflight.short_circuit.status == DispatchStatus.SUCCESS
        assert preflight.reset_performed is False
        assert preflight.halt is False

    def test_prepare_resume_resets_failure_to_pending(self, tmp_path: Path) -> None:
        prepare_resume = self._prepare_resume()
        sp = _seed_state(
            tmp_path,
            [DispatchRecord(name="d1", status=DispatchStatus.FAILURE)],
        )
        preflight = prepare_resume(sp, "d1")
        assert preflight is not None
        assert preflight.reset_performed is True
        state = read_state(sp)
        assert state is not None
        d1 = next(d for d in state.dispatches if d.name == "d1")
        assert d1.status == DispatchStatus.PENDING

    def test_prepare_resume_resets_interrupted_to_pending(self, tmp_path: Path) -> None:
        prepare_resume = self._prepare_resume()
        sp = _seed_state(
            tmp_path,
            [DispatchRecord(name="d1", status=DispatchStatus.INTERRUPTED)],
        )
        preflight = prepare_resume(sp, "d1")
        assert preflight is not None
        assert preflight.reset_performed is True
        state = read_state(sp)
        assert state is not None
        d1 = next(d for d in state.dispatches if d.name == "d1")
        assert d1.status == DispatchStatus.PENDING

    def test_prepare_resume_resets_refused_to_pending(self, tmp_path: Path) -> None:
        prepare_resume = self._prepare_resume()
        sp = _seed_state(
            tmp_path,
            [DispatchRecord(name="d1", status=DispatchStatus.REFUSED)],
        )
        preflight = prepare_resume(sp, "d1")
        assert preflight is not None
        assert preflight.reset_performed is True
        state = read_state(sp)
        assert state is not None
        d1 = next(d for d in state.dispatches if d.name == "d1")
        assert d1.status == DispatchStatus.PENDING

    def test_prepare_resume_passes_through_pending(self, tmp_path: Path) -> None:
        prepare_resume = self._prepare_resume()
        sp = _seed_state(
            tmp_path,
            [
                DispatchRecord(
                    name="d1",
                    status=DispatchStatus.PENDING,
                    session_chain=["sess-A", "sess-B"],
                    dispatched_session_id="sess-A",
                )
            ],
        )
        preflight = prepare_resume(sp, "d1")
        assert preflight is not None
        assert preflight.reset_performed is False
        assert preflight.short_circuit is None
        assert preflight.halt is False
        assert preflight.prior_session_chain == ["sess-A", "sess-B"]
        assert preflight.prior_dispatched_session_id == "sess-A"

    def test_prepare_resume_passes_through_resumable(self, tmp_path: Path) -> None:
        prepare_resume = self._prepare_resume()
        # RESUMABLE without exhausted cap: the chokepoint passes through; cap
        # enforcement is delegated to mark_dispatch_running (L3) — see
        # test_headless_resume_max_attempts.
        sp = _seed_state(
            tmp_path,
            [
                DispatchRecord(
                    name="d1",
                    status=DispatchStatus.RESUMABLE,
                    reason=FEC.FLEET_L3_TIMEOUT,
                    session_chain=["sess-A", "sess-B"],
                    dispatched_session_id="sess-A",
                )
            ],
        )
        preflight = prepare_resume(sp, "d1")
        assert preflight is not None
        assert preflight.short_circuit is None
        assert preflight.reset_performed is False
        assert preflight.halt is False
        assert preflight.prior_session_chain == ["sess-A", "sess-B"]
        assert preflight.prior_dispatched_session_id == "sess-A"

    def test_prepare_resume_returns_none_for_missing_state_fail_open(self, tmp_path: Path) -> None:
        prepare_resume = self._prepare_resume()
        missing = tmp_path / "missing" / "state.json"
        result = prepare_resume(missing, "d1")
        assert result is None

    def test_prepare_resume_halts_on_failure_when_continue_on_failure_false(
        self, tmp_path: Path
    ) -> None:
        prepare_resume = self._prepare_resume()
        sp = _seed_state(
            tmp_path,
            [DispatchRecord(name="d1", status=DispatchStatus.FAILURE)],
        )
        preflight = prepare_resume(sp, "d1", continue_on_failure=False)
        assert preflight is not None
        assert preflight.halt is True
        assert preflight.halted_reason is not None


# ----------------------------------------------------------------------------
# Universal-coverage tests — TestPrepareResumeIsUniversal
# ----------------------------------------------------------------------------


class TestPrepareResumeIsUniversal:
    """Enforces that all three resume entry points call ``prepare_resume``.

    The chokepoint is only useful if every entry point funnels through it.
    """

    def test_prepare_resume_is_called_by_execute_dispatch_resume_branch(self) -> None:
        from autoskillit.fleet import _api

        # Read the resume branch in execute_dispatch (the open_continued block).
        src = inspect.getsource(_api._run_dispatch)
        assert "prepare_resume" in src, "execute_dispatch resume branch must call prepare_resume"

    def test_prepare_resume_is_called_by_dispatch_food_truck_mcp(self) -> None:
        try:
            from autoskillit.server.tools import tools_fleet_dispatch
        except Exception:
            pytest.skip("server.tools not importable in this environment")
        src = inspect.getsource(tools_fleet_dispatch.dispatch_food_truck)
        assert "prepare_resume" in src, "dispatch_food_truck must call prepare_resume"

    def test_prepare_resume_is_called_by_resume_campaign_from_state(self) -> None:
        from autoskillit.fleet import state_recovery

        src = inspect.getsource(state_recovery.resume_campaign_from_state)
        assert "prepare_resume" in src, "resume_campaign_from_state must call prepare_resume"


# ----------------------------------------------------------------------------
# ResumePreflight shape
# ----------------------------------------------------------------------------


class TestResumePreflightShape:
    def test_resume_preflight_namedtuple_fields(self) -> None:
        # NamedTuple field set is part of the public contract.
        expected = {
            "prior_session_chain",
            "prior_dispatched_session_id",
            "short_circuit",
            "reset_performed",
            "halt",
            "halted_reason",
        }
        actual = set(ResumePreflight._fields)
        assert expected.issubset(actual), (
            f"ResumePreflight must expose fields {expected}; got {actual}"
        )


# Sentinel to silence "unused import" complaints for the FLEET_HALTED_SENTINEL
# symbol — referenced by sibling test modules and re-exported for completeness.
_ = FLEET_HALTED_SENTINEL
