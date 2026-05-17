"""Tests for FLEET_L3_TIMEOUT infrastructure classification and exhaustiveness guard."""

from __future__ import annotations

import pytest

from autoskillit.core import FleetErrorCode as FEC
from autoskillit.fleet.state_recovery import has_failed_dispatch
from autoskillit.fleet.state_types import CampaignState, DispatchRecord, DispatchStatus

pytestmark = [pytest.mark.layer("fleet"), pytest.mark.small, pytest.mark.feature("fleet")]


class TestTimeoutIsInfrastructureFailure:
    def test_timeout_is_infrastructure_failure(self, tmp_path):
        """FLEET_L3_TIMEOUT must be classified as infrastructure — does not halt campaign."""
        from autoskillit.fleet.state import _write_state as write_state

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
        from autoskillit.fleet.state_types import (
            ErrorCodeCategory,
            get_error_category,
        )

        for code in FEC:
            if code.startswith("fleet_"):
                category = get_error_category(code)
                assert category in (
                    ErrorCodeCategory.INFRASTRUCTURE,
                    ErrorCodeCategory.LOGIC,
                ), f"{code} has no explicit category"
