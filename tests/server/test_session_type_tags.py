"""Tests for _collect_fleet_tool_tags in server._session_type (Finding 1)."""

from __future__ import annotations

import pytest

pytestmark = [pytest.mark.layer("server"), pytest.mark.small]


# ---------------------------------------------------------------------------
# T5: fleet sessions expose only the fleet feature surface.
# ---------------------------------------------------------------------------


def test_collect_fleet_tool_tags_is_fleet_feature_surface() -> None:
    """_collect_fleet_tool_tags() excludes unrelated feature tags."""
    from autoskillit.core.types._type_constants_features import FEATURE_REGISTRY
    from autoskillit.server._session_type import _collect_fleet_tool_tags

    expected = FEATURE_REGISTRY["fleet"].tool_tags
    assert _collect_fleet_tool_tags() == expected


def test_collect_fleet_tool_tags_includes_fleet() -> None:
    """The 'fleet' tag from the fleet FeatureDef must appear in the collected tags."""
    from autoskillit.server._session_type import _collect_fleet_tool_tags

    assert "fleet" in _collect_fleet_tool_tags()
