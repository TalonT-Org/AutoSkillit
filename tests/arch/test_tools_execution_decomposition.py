"""Structural decomposition guard for tools_execution.py split."""

from __future__ import annotations

import pytest

from autoskillit.server.tools import tools_execution  # noqa: F401

pytestmark = [pytest.mark.layer("arch"), pytest.mark.small]


class TestToolsExecutionDecomposition:
    def test_dispatch_food_truck_not_in_tools_execution(self):
        """dispatch_food_truck must live in tools_fleet_dispatch, not tools_execution."""
        assert not hasattr(tools_execution, "dispatch_food_truck")

    def test_record_gate_dispatch_not_in_tools_execution(self):
        """record_gate_dispatch must live in tools_fleet_dispatch, not tools_execution."""
        assert not hasattr(tools_execution, "record_gate_dispatch")

    def test_dispatch_food_truck_in_tools_fleet_dispatch(self):
        """dispatch_food_truck must be importable from tools_fleet_dispatch."""
        from autoskillit.server.tools.tools_fleet_dispatch import dispatch_food_truck

        assert callable(dispatch_food_truck)

    def test_record_gate_dispatch_in_tools_fleet_dispatch(self):
        """record_gate_dispatch must be importable from tools_fleet_dispatch."""
        from autoskillit.server.tools.tools_fleet_dispatch import record_gate_dispatch

        assert callable(record_gate_dispatch)

    def test_import_and_call_in_helpers(self):
        """_import_and_call must be defined in _execution_helpers."""
        from autoskillit.server.tools._execution_helpers import _import_and_call

        assert callable(_import_and_call)

    def test_coerce_scalar_in_helpers(self):
        """_coerce_scalar must be importable from _execution_helpers, not tools_execution."""
        from autoskillit.server.tools._execution_helpers import _coerce_scalar

        assert callable(_coerce_scalar)
        assert not hasattr(tools_execution, "_coerce_scalar")
