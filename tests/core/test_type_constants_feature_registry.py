"""Tests for FEATURE_REGISTRY, FeatureDef, and feature-flag lifecycle."""

from __future__ import annotations

import pytest

pytestmark = [pytest.mark.layer("core"), pytest.mark.small]


def test_feature_reveal_tags_removed() -> None:
    """FEATURE_REVEAL_TAGS was intentionally removed in #1141."""
    with pytest.raises(ImportError):
        from autoskillit.core import FEATURE_REVEAL_TAGS  # noqa: F401


def test_exclusive_feature_tools_removed() -> None:
    """EXCLUSIVE_FEATURE_TOOLS was removed (issue #1150) — must not be importable."""
    import autoskillit.core as core

    assert not hasattr(core, "EXCLUSIVE_FEATURE_TOOLS")


def test_exclusive_feature_tools_not_in_all() -> None:
    """EXCLUSIVE_FEATURE_TOOLS must not appear in _type_constants.__all__."""
    from autoskillit.core.types import _type_constants

    assert "EXCLUSIVE_FEATURE_TOOLS" not in _type_constants.__all__  # type: ignore[attr-defined]


def test_fleet_default_enabled_is_false() -> None:
    """Fleet is gated off by default — enabled only via project config."""
    from autoskillit.core import FEATURE_REGISTRY

    assert FEATURE_REGISTRY["fleet"].default_enabled is False


def test_exploration_feature_definition_pins_loading_and_visibility_policy() -> None:
    from autoskillit.core import FEATURE_REGISTRY

    definition = FEATURE_REGISTRY["exploration"]

    assert definition.tier == 1
    assert definition.import_package == "autoskillit.exploration"
    assert definition.tool_tags == frozenset({"exploration"})
    assert definition.default_enabled is False
    assert definition.requires_backend_alignment is False


def test_is_feature_enabled_fleet_defaults_false() -> None:
    """Without explicit config, fleet resolves to disabled when experimental_enabled=False."""
    from autoskillit.core.feature_flags import is_feature_enabled

    assert is_feature_enabled("fleet", {}, experimental_enabled=False) is False
    # fleet is EXPERIMENTAL, so blanket enables it
    assert is_feature_enabled("fleet", {}, experimental_enabled=True) is True


# ---------------------------------------------------------------------------
# T1: FeatureDef has no redundant name field (Finding 3)
# ---------------------------------------------------------------------------


def test_feature_def_has_no_name_field() -> None:
    """FeatureDef.name is redundant with the FEATURE_REGISTRY dict key and must not exist."""
    import dataclasses

    from autoskillit.core.types._type_constants_features import FeatureDef

    field_names = {f.name for f in dataclasses.fields(FeatureDef)}
    assert "name" not in field_names, "FeatureDef.name is redundant with FEATURE_REGISTRY dict key"
