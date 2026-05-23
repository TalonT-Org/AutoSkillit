"""Structural guard: validates test file split imports."""

import pytest

pytestmark = [pytest.mark.layer("server"), pytest.mark.small]


def test_dispatch_tool_importable():
    from autoskillit.server.tools.tools_fleet_dispatch import dispatch_food_truck

    assert callable(dispatch_food_truck)


def test_execute_dispatch_importable():
    from autoskillit.fleet._api import execute_dispatch

    assert callable(execute_dispatch)
