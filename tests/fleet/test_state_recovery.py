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


class TestResumableToFailureEscalation:
    def test_resumable_escalation_to_failure_leaves_labels_uncleaned(self, tmp_path: Path) -> None:
        """When RESUMABLE exhausts attempts, dispatch becomes FAILURE with labels_cleaned=False."""
        from autoskillit.core import FleetErrorCode
        from autoskillit.fleet.state import upsert_dispatch_record_by_name, write_initial_state
        from autoskillit.fleet.state_recovery import (
            MAX_CONSECUTIVE_RESUME_ATTEMPTS,
            resume_campaign_from_state,
        )

        state_path = tmp_path / "escalation.json"
        write_initial_state(
            state_path,
            campaign_id="test-esc",
            campaign_name="test-escalation",
            manifest_path="",
            dispatches=[DispatchRecord(name="d1")],
        )
        timeout_history = [
            {"status": str(DispatchStatus.RESUMABLE), "reason": FleetErrorCode.FLEET_L3_TIMEOUT}
            for _ in range(MAX_CONSECUTIVE_RESUME_ATTEMPTS)
        ]
        upsert_dispatch_record_by_name(
            state_path,
            DispatchRecord(
                name="d1",
                status=DispatchStatus.RESUMABLE,
                dispatch_id="esc-uuid",
                dispatched_session_id="esc-sess",
                attempt_history=timeout_history,
                sidecar_path=str(tmp_path / "esc_sidecar.jsonl"),
            ),
        )

        decision = resume_campaign_from_state(state_path, continue_on_failure=False)
        assert decision is not None
        assert decision.next_dispatch_name == ""

        from autoskillit.fleet.state import read_state

        state = read_state(state_path)
        assert state is not None
        d = state.dispatches[0]
        assert d.status == DispatchStatus.FAILURE
        assert d.labels_cleaned is False

    @pytest.mark.anyio
    async def test_sweep_recovers_labels_from_escalated_failure(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Startup sweep cleans labels for a FAILURE dispatch produced by RESUMABLE escalation."""
        import json
        from unittest.mock import AsyncMock

        from autoskillit.core import FleetErrorCode
        from autoskillit.fleet.state import upsert_dispatch_record_by_name, write_initial_state
        from autoskillit.fleet.state_recovery import (
            MAX_CONSECUTIVE_RESUME_ATTEMPTS,
            resume_campaign_from_state,
        )

        monkeypatch.setattr(
            "autoskillit.fleet._label_cleanup.is_dispatch_session_alive",
            lambda record: False,
        )

        state_path = tmp_path / "esc_sweep.json"
        sidecar = tmp_path / "esc_sweep_sidecar.jsonl"
        sidecar.write_text(
            json.dumps(
                {
                    "issue_url": "https://github.com/owner/repo/issues/99",
                    "status": "completed",
                    "ts": "2026-01-01T00:00:00Z",
                }
            )
            + "\n"
        )
        write_initial_state(
            state_path,
            campaign_id="test-esc-sweep",
            campaign_name="test-escalation-sweep",
            manifest_path="",
            dispatches=[DispatchRecord(name="d1")],
        )
        timeout_history = [
            {"status": str(DispatchStatus.RESUMABLE), "reason": FleetErrorCode.FLEET_L3_TIMEOUT}
            for _ in range(MAX_CONSECUTIVE_RESUME_ATTEMPTS)
        ]
        upsert_dispatch_record_by_name(
            state_path,
            DispatchRecord(
                name="d1",
                status=DispatchStatus.RESUMABLE,
                dispatch_id="esc-uuid",
                dispatched_session_id="esc-sess",
                attempt_history=timeout_history,
                sidecar_path=str(sidecar),
            ),
        )

        resume_campaign_from_state(state_path, continue_on_failure=False)

        from autoskillit.fleet._label_cleanup import sweep_stale_dispatch_labels
        from autoskillit.fleet.state import read_state

        swap_labels_mock = AsyncMock(return_value={"success": True})
        github_client = AsyncMock()
        github_client.swap_labels = swap_labels_mock

        await sweep_stale_dispatch_labels([state_path], github_client)

        swap_labels_mock.assert_called_once()
        state = read_state(state_path)
        assert state is not None
        assert state.dispatches[0].labels_cleaned is True


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
