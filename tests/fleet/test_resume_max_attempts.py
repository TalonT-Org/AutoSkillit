"""Tests for the max_resume_attempts guard in resume_campaign_from_state."""

from __future__ import annotations

import pytest

from autoskillit.core import FleetErrorCode as FEC
from autoskillit.fleet import DispatchStatus
from autoskillit.fleet.state import _write_state as write_state
from autoskillit.fleet.state_recovery import (
    resume_campaign_from_state,
)
from autoskillit.fleet.state_types import CampaignState, DispatchRecord

pytestmark = [pytest.mark.layer("fleet"), pytest.mark.small, pytest.mark.feature("fleet")]


class TestMaxResumeAttemptsGuard:
    def test_resumable_below_limit_is_returned(self, tmp_path):
        """RESUMABLE dispatch with fewer than MAX attempts is returned for resume."""
        dispatches = [
            DispatchRecord(
                name="step_1",
                status=DispatchStatus.RESUMABLE,
                reason=FEC.FLEET_L3_TIMEOUT,
                dispatched_session_id="sess-abc",
                attempt_history=[
                    {"status": str(DispatchStatus.RESUMABLE), "reason": FEC.FLEET_L3_TIMEOUT},
                ],
            ),
        ]
        state = CampaignState(
            schema_version=5,
            campaign_id="test-id",
            campaign_name="test",
            manifest_path="manifest.yaml",
            started_at=0.0,
            dispatches=dispatches,
        )
        write_state(tmp_path / "state.json", state)

        decision = resume_campaign_from_state(tmp_path, continue_on_failure=False)
        assert decision is not None
        assert decision.is_resumable
        assert decision.next_dispatch_name == "step_1"

    def test_resumable_at_limit_is_converted_to_failure(self, tmp_path):
        """RESUMABLE dispatch with MAX consecutive timeout attempts → FAILURE (campaign halts)."""
        dispatches = [
            DispatchRecord(
                name="step_1",
                status=DispatchStatus.RESUMABLE,
                reason=FEC.FLEET_L3_TIMEOUT,
                dispatched_session_id="sess-abc",
                attempt_history=[
                    {"status": str(DispatchStatus.RESUMABLE), "reason": FEC.FLEET_L3_TIMEOUT},
                    {"status": str(DispatchStatus.RESUMABLE), "reason": FEC.FLEET_L3_TIMEOUT},
                    {"status": str(DispatchStatus.RESUMABLE), "reason": FEC.FLEET_L3_TIMEOUT},
                ],
            ),
        ]
        state = CampaignState(
            schema_version=5,
            campaign_id="test-id",
            campaign_name="test",
            manifest_path="manifest.yaml",
            started_at=0.0,
            dispatches=dispatches,
        )
        write_state(tmp_path / "state.json", state)

        decision = resume_campaign_from_state(tmp_path, continue_on_failure=False)
        assert decision is not None
        # Exceeded retry budget → halted
        assert decision.completed_dispatches_block == "fleet_halted_on_failure"

    def test_resumable_above_limit_is_converted_to_failure(self, tmp_path):
        """RESUMABLE with more than MAX attempts → FAILURE."""
        dispatches = [
            DispatchRecord(
                name="step_1",
                status=DispatchStatus.RESUMABLE,
                reason=FEC.FLEET_L3_TIMEOUT,
                dispatched_session_id="sess-abc",
                attempt_history=[
                    {"status": str(DispatchStatus.RESUMABLE), "reason": FEC.FLEET_L3_TIMEOUT},
                    {"status": str(DispatchStatus.RESUMABLE), "reason": FEC.FLEET_L3_TIMEOUT},
                    {"status": str(DispatchStatus.RESUMABLE), "reason": FEC.FLEET_L3_TIMEOUT},
                    {"status": str(DispatchStatus.RESUMABLE), "reason": FEC.FLEET_L3_TIMEOUT},
                ],
            ),
        ]
        state = CampaignState(
            schema_version=5,
            campaign_id="test-id",
            campaign_name="test",
            manifest_path="manifest.yaml",
            started_at=0.0,
            dispatches=dispatches,
        )
        write_state(tmp_path / "state.json", state)

        decision = resume_campaign_from_state(tmp_path, continue_on_failure=False)
        assert decision is not None
        assert decision.completed_dispatches_block == "fleet_halted_on_failure"
