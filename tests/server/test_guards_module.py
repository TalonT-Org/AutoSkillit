"""Smoke test: all 6 guards are importable from _guards."""

import pytest

pytestmark = [pytest.mark.layer("server"), pytest.mark.small]


def test_require_enabled_importable():
    from autoskillit.server._guards import _require_enabled

    assert callable(_require_enabled)


def test_require_orchestrator_or_higher_importable():
    from autoskillit.server._guards import _require_orchestrator_or_higher

    assert callable(_require_orchestrator_or_higher)


def test_require_orchestrator_exact_importable():
    from autoskillit.server._guards import _require_orchestrator_exact

    assert callable(_require_orchestrator_exact)


def test_require_fleet_importable():
    from autoskillit.server._guards import _require_fleet

    assert callable(_require_fleet)


def test_check_dry_walkthrough_importable():
    from autoskillit.server._guards import _check_dry_walkthrough

    assert callable(_check_dry_walkthrough)


def test_validate_skill_command_importable():
    from autoskillit.server._guards import _validate_skill_command

    assert callable(_validate_skill_command)
