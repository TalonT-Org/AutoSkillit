"""Tests for dispatch identity continuity on resume — _api.py changes."""

from __future__ import annotations

import inspect

import pytest

pytestmark = [pytest.mark.layer("fleet"), pytest.mark.small, pytest.mark.feature("fleet")]


class TestExecuteDispatchPriorDispatchId:
    async def test_execute_dispatch_accepts_prior_dispatch_id_parameter(self) -> None:
        """execute_dispatch must accept prior_dispatch_id parameter."""
        from autoskillit.fleet._api import execute_dispatch

        sig = inspect.signature(execute_dispatch)
        assert "prior_dispatch_id" in sig.parameters

    async def test_run_dispatch_accepts_prior_dispatch_id_parameter(self) -> None:
        """_run_dispatch must accept prior_dispatch_id parameter."""
        from autoskillit.fleet._api import _run_dispatch

        sig = inspect.signature(_run_dispatch)
        assert "prior_dispatch_id" in sig.parameters


class TestDispatchFoodTruckPriorDispatchId:
    async def test_dispatch_food_truck_accepts_prior_dispatch_id_parameter(self) -> None:
        """dispatch_food_truck tool must accept prior_dispatch_id parameter."""
        from autoskillit.server.tools.tools_fleet_dispatch import dispatch_food_truck

        sig = inspect.signature(dispatch_food_truck)
        assert "prior_dispatch_id" in sig.parameters


class TestFleetSessionPromptPriorDispatchId:
    async def test_build_fleet_campaign_prompt_accepts_prior_dispatch_id_parameter(
        self,
    ) -> None:
        """_build_fleet_campaign_prompt must accept prior_dispatch_id parameter."""
        from autoskillit.cli._prompts_campaign import _build_fleet_campaign_prompt

        sig = inspect.signature(_build_fleet_campaign_prompt)
        assert "prior_dispatch_id" in sig.parameters
