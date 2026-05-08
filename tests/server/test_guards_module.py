"""Tests that _guards functions document their orchestration level requirements."""

import pytest

pytestmark = [pytest.mark.layer("server"), pytest.mark.small]


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
