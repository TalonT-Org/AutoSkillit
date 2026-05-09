"""Tests for derive_orchestrator_resume_spec in state_recovery module."""

from __future__ import annotations

from pathlib import Path

import pytest

from autoskillit.core import NamedResume, NoResume
from autoskillit.fleet import CampaignState, DispatchRecord, DispatchStatus
from autoskillit.fleet.state_types import ResumeDecision

pytestmark = [pytest.mark.layer("fleet"), pytest.mark.small, pytest.mark.feature("fleet")]


def _make_state(
    *,
    orchestrator_session_id: str = "",
    dispatches: list[DispatchRecord] | None = None,
) -> CampaignState:
    if dispatches is None:
        dispatches = [DispatchRecord(name="dispatch-1")]
    return CampaignState(
        schema_version=4,
        campaign_id="test-id",
        campaign_name="test-campaign",
        manifest_path="manifest.yaml",
        started_at=0.0,
        dispatches=dispatches,
        orchestrator_session_id=orchestrator_session_id,
    )


class TestDeriveOrchestratorResumeSpec:
    def test_derive_returns_named_resume_when_session_id_present(self) -> None:
        from autoskillit.fleet.state_recovery import derive_orchestrator_resume_spec

        state = _make_state(orchestrator_session_id="prior-session-xyz")
        result = derive_orchestrator_resume_spec(state)
        assert result == NamedResume(session_id="prior-session-xyz")

    def test_derive_returns_no_resume_when_session_id_empty(self) -> None:
        from autoskillit.fleet.state_recovery import derive_orchestrator_resume_spec

        state = _make_state(orchestrator_session_id="")
        result = derive_orchestrator_resume_spec(state)
        assert result == NoResume()

    def test_derive_falls_back_to_caller_session_id(self) -> None:
        from autoskillit.fleet.state_recovery import derive_orchestrator_resume_spec

        dispatches = [
            DispatchRecord(
                name="dispatch-1",
                status=DispatchStatus.SUCCESS,
                caller_session_id="caller-sess-fallback",
            ),
        ]
        state = _make_state(orchestrator_session_id="", dispatches=dispatches)
        result = derive_orchestrator_resume_spec(state)
        assert result == NamedResume(session_id="caller-sess-fallback")

    def test_derive_prefers_orchestrator_session_id_over_caller_session_id(self) -> None:
        from autoskillit.fleet.state_recovery import derive_orchestrator_resume_spec

        dispatches = [
            DispatchRecord(
                name="dispatch-1",
                status=DispatchStatus.SUCCESS,
                caller_session_id="caller-sess-fallback",
            ),
        ]
        state = _make_state(
            orchestrator_session_id="orchestrator-sess-primary", dispatches=dispatches
        )
        result = derive_orchestrator_resume_spec(state)
        assert result == NamedResume(session_id="orchestrator-sess-primary")

    def test_derive_returns_no_resume_when_no_session_ids_at_all(self) -> None:
        from autoskillit.fleet.state_recovery import derive_orchestrator_resume_spec

        dispatches = [
            DispatchRecord(name="dispatch-1", status=DispatchStatus.SUCCESS),
        ]
        state = _make_state(orchestrator_session_id="", dispatches=dispatches)
        result = derive_orchestrator_resume_spec(state)
        assert result == NoResume()

    def test_derive_uses_latest_dispatch_caller_session_id(self) -> None:
        from autoskillit.fleet.state_recovery import derive_orchestrator_resume_spec

        dispatches = [
            DispatchRecord(
                name="dispatch-1",
                status=DispatchStatus.SUCCESS,
                caller_session_id="older-caller-sess",
            ),
            DispatchRecord(
                name="dispatch-2",
                status=DispatchStatus.RUNNING,
                caller_session_id="latest-caller-sess",
            ),
        ]
        state = _make_state(orchestrator_session_id="", dispatches=dispatches)
        result = derive_orchestrator_resume_spec(state)
        assert result == NamedResume(session_id="latest-caller-sess")

    def test_derive_falls_back_for_pending_dispatch(self) -> None:
        from autoskillit.fleet.state_recovery import derive_orchestrator_resume_spec

        dispatches = [
            DispatchRecord(
                name="dispatch-1",
                status=DispatchStatus.PENDING,
                caller_session_id="pending-caller-sess",
            ),
        ]
        state = _make_state(orchestrator_session_id="", dispatches=dispatches)
        result = derive_orchestrator_resume_spec(state)
        assert result == NamedResume(session_id="pending-caller-sess")

    def test_derive_returns_no_resume_for_pending_dispatch_without_caller_session_id(
        self,
    ) -> None:
        from autoskillit.fleet.state_recovery import derive_orchestrator_resume_spec

        dispatches = [
            DispatchRecord(name="dispatch-1", status=DispatchStatus.PENDING),
        ]
        state = _make_state(orchestrator_session_id="", dispatches=dispatches)
        result = derive_orchestrator_resume_spec(state)
        assert result == NoResume()


class TestResumeDecisionDispatchId:
    def test_resume_decision_carries_dispatch_id(self) -> None:
        """ResumeDecision must include the original dispatch_id for identity continuity."""
        decision = ResumeDecision(
            next_dispatch_name="fix-bug",
            completed_dispatches_block="",
            is_resumable=True,
            dispatched_session_id="session-abc",
            dispatch_id="original-uuid-A",
        )
        assert decision.dispatch_id == "original-uuid-A"

    def test_resume_decision_dispatch_id_defaults_to_empty_string(self) -> None:
        """dispatch_id should default to empty string for backward compatibility."""
        decision = ResumeDecision(
            next_dispatch_name="fix-bug",
            completed_dispatches_block="",
            is_resumable=False,
            dispatched_session_id="",
        )
        assert decision.dispatch_id == ""


class TestResumeCampaignFromStateDispatchId:
    async def test_resume_campaign_populates_dispatch_id(self, tmp_path: Path) -> None:
        """When a RESUMABLE dispatch is found, its dispatch_id must appear in ResumeDecision."""
        from autoskillit.fleet.state import upsert_dispatch_record_by_name, write_initial_state

        state_path = tmp_path / "test_state.json"
        # Create initial state with a pending dispatch
        write_initial_state(
            state_path,
            campaign_id="test-campaign",
            campaign_name="fix-issue",
            manifest_path="",
            dispatches=[DispatchRecord(name="fix-issue")],
        )
        # Upsert it to RESUMABLE with a dispatch_id
        upsert_dispatch_record_by_name(
            state_path,
            DispatchRecord(
                name="fix-issue",
                status=DispatchStatus.RESUMABLE,
                dispatch_id="original-uuid-A",
                dispatched_session_id="session-abc",
            ),
        )
        from autoskillit.fleet.state_recovery import resume_campaign_from_state

        decision = resume_campaign_from_state(state_path, continue_on_failure=False)
        assert decision is not None
        assert decision.dispatch_id == "original-uuid-A"

    async def test_resume_campaign_returns_none_for_missing_state(self, tmp_path: Path) -> None:
        """Missing state file should return None, not raise."""
        from autoskillit.fleet.state_recovery import resume_campaign_from_state

        state_path = tmp_path / "nonexistent.json"
        decision = resume_campaign_from_state(state_path, continue_on_failure=False)
        assert decision is None
