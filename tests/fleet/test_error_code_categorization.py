"""Tests for FLEET_L3_TIMEOUT infrastructure classification and exhaustiveness guard."""

from __future__ import annotations

import pytest

from autoskillit.core import FaultDomain
from autoskillit.core import FleetErrorCode as FEC
from autoskillit.fleet.campaign_state.state_records import CampaignState, DispatchRecord
from autoskillit.fleet.campaign_state.state_recovery import (
    has_blocking_dispatch,
    has_failed_dispatch,
)
from autoskillit.fleet.campaign_state.state_transitions import DispatchStatus

pytestmark = [pytest.mark.layer("fleet"), pytest.mark.small, pytest.mark.feature("fleet")]


class TestTimeoutIsInfrastructureFailure:
    def test_timeout_is_infrastructure_failure(self, tmp_path):
        """FLEET_L3_TIMEOUT must be classified as infrastructure — does not halt campaign."""
        from autoskillit.fleet.campaign_state.state import _write_state as write_state

        dispatches = [
            DispatchRecord(
                name="step_1",
                status=DispatchStatus.FAILURE,
                reason=FEC.FLEET_L3_TIMEOUT,
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
        # Infrastructure failures do not halt campaigns
        assert not has_failed_dispatch(tmp_path / "state.json")


class TestAllFleetErrorCodesHaveCategory:
    def test_all_fleet_error_codes_have_infrastructure_or_logic_category(self):
        """Every FleetErrorCode must have an explicit infrastructure/logic classification."""
        from autoskillit.fleet.campaign_state.state_error_codes import (
            _ERROR_CODE_CATEGORIES,
            get_error_category,
        )

        for code in FEC:
            if code.startswith("fleet_"):
                assert code in _ERROR_CODE_CATEGORIES, f"{code} has no explicit category entry"
                category = get_error_category(code)
                assert category in (
                    FaultDomain.INFRASTRUCTURE,
                    FaultDomain.LOGIC,
                ), f"{code} has no explicit category"

    @pytest.mark.parametrize(
        "code",
        [
            FEC.FLEET_L3_RESULT_CONTRACT_VIOLATION,
            FEC.FLEET_RESET_NOT_FOUND,
            FEC.FLEET_RESET_INVALID_TARGET,
            FEC.FLEET_RESET_STILL_RUNNING,
            FEC.FLEET_INVALID_BACKEND,
        ],
    )
    def test_logic_error_codes_are_not_infrastructure_recovery(self, code):
        from autoskillit.fleet.campaign_state.state_error_codes import (
            _ERROR_CODE_CATEGORIES,
            _INFRASTRUCTURE_FAILURE_REASONS,
            get_error_category,
        )

        assert _ERROR_CODE_CATEGORIES[code] is FaultDomain.LOGIC
        assert get_error_category(code) is FaultDomain.LOGIC
        assert code not in _INFRASTRUCTURE_FAILURE_REASONS

    def test_context_exhaustion_is_logic_and_blocks_campaign_resume(self, tmp_path):
        from autoskillit.fleet.campaign_state.state import _write_state as write_state
        from autoskillit.fleet.campaign_state.state_error_codes import (
            _ERROR_CODE_CATEGORIES,
            _INFRASTRUCTURE_FAILURE_REASONS,
            get_error_category,
        )

        code = FEC.FLEET_L3_CONTEXT_EXHAUSTED
        state_path = tmp_path / "state.json"
        state = CampaignState(
            schema_version=5,
            campaign_id="test-context-id",
            campaign_name="test-context",
            manifest_path="manifest.yaml",
            started_at=0.0,
            dispatches=[
                DispatchRecord(
                    name="step_1",
                    status=DispatchStatus.FAILURE,
                    reason=code,
                )
            ],
        )
        write_state(state_path, state)

        assert _ERROR_CODE_CATEGORIES[code] is FaultDomain.LOGIC
        assert get_error_category(code) is FaultDomain.LOGIC
        assert code not in _INFRASTRUCTURE_FAILURE_REASONS
        assert has_failed_dispatch(state_path)
        assert has_blocking_dispatch(state_path)
