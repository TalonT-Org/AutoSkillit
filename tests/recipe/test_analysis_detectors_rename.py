"""Verify plan-visualization → synthesize-vis-plan rename in _OBSERVABILITY_CAPTURES."""

import pytest

from autoskillit.recipe._analysis_detectors import _OBSERVABILITY_CAPTURES

pytestmark = [pytest.mark.layer("recipe"), pytest.mark.small]


def test_observability_captures_uses_synthesize_vis_plan():
    """synthesize-vis-plan fragment replaces plan-visualization."""
    fragments = {frag for _, frag in _OBSERVABILITY_CAPTURES}
    assert "synthesize-vis-plan" in fragments
    assert "plan-visualization" not in fragments


def test_observability_captures_vis_plan_tuples():
    """Both visualization_plan_path and report_plan_path map to synthesize-vis-plan."""
    assert ("visualization_plan_path", "synthesize-vis-plan") in _OBSERVABILITY_CAPTURES
    assert ("report_plan_path", "synthesize-vis-plan") in _OBSERVABILITY_CAPTURES
