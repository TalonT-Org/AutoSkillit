"""Tests for dispatch_food_truck preflight integration.

Verifies that the fleet dispatch path references the shared
_check_dispatch_feasibility function and that the preflight
runs before execute_dispatch.
"""

from __future__ import annotations

import inspect

pytestmark = [pytest.mark.layer("server"), pytest.mark.small]


class TestFleetDispatchPreflightWiring:
    """Structural tests confirming preflight is wired into dispatch_food_truck."""

    def test_dispatch_food_truck_calls_preflight(self) -> None:
        """dispatch_food_truck source must call _check_dispatch_feasibility."""
        from autoskillit.server.tools import tools_fleet_dispatch

        source = inspect.getsource(tools_fleet_dispatch)
        assert "_check_dispatch_feasibility" in source, (
            "dispatch_food_truck must call _check_dispatch_feasibility"
        )

    def test_preflight_called_before_execute_dispatch(self) -> None:
        """In the source order, _check_dispatch_feasibility must appear before
        the execute_dispatch call."""
        from autoskillit.server.tools import tools_fleet_dispatch

        source = inspect.getsource(tools_fleet_dispatch)
        preflight_pos = source.find("_check_dispatch_feasibility")
        execute_pos = source.find("execute_dispatch(")
        assert preflight_pos > 0
        assert execute_pos > 0
        assert preflight_pos < execute_pos, (
            f"Preflight must be called before execute_dispatch "
            f"(preflight at {preflight_pos}, execute at {execute_pos})"
        )
