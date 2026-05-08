"""Tests for derive_orchestrator_resume_spec in state_recovery module."""

from __future__ import annotations

import pytest

from autoskillit.core import NamedResume, NoResume
from autoskillit.fleet import CampaignState, DispatchRecord, DispatchStatus

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
