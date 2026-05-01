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


def test_require_orchestrator_or_higher_doc_mentions_l1():
    from autoskillit.server._guards import _require_orchestrator_or_higher

    doc = _require_orchestrator_or_higher.__doc__ or ""
    assert "L1" in doc
    assert "L2" in doc
    assert "L3" in doc


def test_require_orchestrator_exact_doc_mentions_l2():
    from autoskillit.server._guards import _require_orchestrator_exact

    doc = _require_orchestrator_exact.__doc__ or ""
    assert "L2" in doc
    assert "L1" in doc
    assert "L3" in doc


def test_require_fleet_doc_mentions_l3():
    from autoskillit.server._guards import _require_fleet

    doc = _require_fleet.__doc__ or ""
    assert "L3" in doc
    assert "L1" in doc
    assert "L2" in doc
