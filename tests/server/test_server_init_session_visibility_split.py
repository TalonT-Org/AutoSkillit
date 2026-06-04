"""Structural guard: validates session visibility test file split imports."""

import pytest

pytestmark = [pytest.mark.layer("server"), pytest.mark.small]


def test_apply_session_type_visibility_importable():
    from autoskillit.server._session_type import _apply_session_type_visibility

    assert callable(_apply_session_type_visibility)


def test_fleet_auto_gate_boot_importable():
    from autoskillit.server._lifespan import _fleet_auto_gate_boot

    assert callable(_fleet_auto_gate_boot)


def test_skill_auto_gate_boot_importable():
    from autoskillit.server._lifespan import _skill_auto_gate_boot

    assert callable(_skill_auto_gate_boot)
