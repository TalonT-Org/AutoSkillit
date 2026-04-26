"""Tests for _collect_fleet_tool_tags in server._session_type (Finding 1)."""

from __future__ import annotations

import pytest

pytestmark = [pytest.mark.layer("server"), pytest.mark.small]


# ---------------------------------------------------------------------------
# T5: _collect_fleet_tool_tags auto-discovers tags from FEATURE_REGISTRY
# ---------------------------------------------------------------------------


def test_collect_fleet_tool_tags_is_union_of_registry() -> None:
    """_collect_fleet_tool_tags() equals the union of all FeatureDef.tool_tags."""
    from autoskillit.core._type_constants import FEATURE_REGISTRY
    from autoskillit.server._session_type import _collect_fleet_tool_tags

    expected = frozenset().union(*(fdef.tool_tags for fdef in FEATURE_REGISTRY.values()))
    assert _collect_fleet_tool_tags() == expected


def test_collect_fleet_tool_tags_includes_fleet() -> None:
    """The 'fleet' tag from the fleet FeatureDef must appear in the collected tags."""
    from autoskillit.server._session_type import _collect_fleet_tool_tags

    assert "fleet" in _collect_fleet_tool_tags()
