"""Tests for core/feature_flags.py — _collect_disabled_feature_tags helper."""

from __future__ import annotations

import pytest

pytestmark = [pytest.mark.layer("core"), pytest.mark.small]


class TestCollectDisabledFeatureTags:
    def test_returns_frozenset(self):
        from autoskillit.core.feature_flags import _collect_disabled_feature_tags

        result = _collect_disabled_feature_tags({})
        assert isinstance(result, frozenset)

    def test_disabled_fleet_returns_fleet_tag(self):
        from autoskillit.core.feature_flags import _collect_disabled_feature_tags

        result = _collect_disabled_feature_tags({"fleet": False})
        assert "fleet" in result

    def test_enabled_fleet_not_in_result(self):
        from autoskillit.core.feature_flags import _collect_disabled_feature_tags

        result = _collect_disabled_feature_tags({"fleet": True})
        assert "fleet" not in result

    def test_planner_skipped_empty_tool_tags(self):
        from autoskillit.core.feature_flags import _collect_disabled_feature_tags

        # "planner" FeatureDef has tool_tags=frozenset() — must be skipped
        result = _collect_disabled_feature_tags({"fleet": False, "planner": False})
        assert result == frozenset({"fleet"})

    def test_default_disabled_fleet(self):
        from autoskillit.core.feature_flags import _collect_disabled_feature_tags

        # fleet.default_enabled is False → fleet disabled when key absent
        result = _collect_disabled_feature_tags({})
        assert "fleet" in result

    def test_hypothetical_third_feature_auto_discovered(self, monkeypatch):
        from autoskillit.core import feature_flags as ff
        from autoskillit.core._type_constants import FEATURE_REGISTRY, FeatureDef, FeatureLifecycle
        from autoskillit.core.feature_flags import _collect_disabled_feature_tags

        fake_def = FeatureDef(
            lifecycle=FeatureLifecycle.EXPERIMENTAL,
            description="Test-only gate",
            tool_tags=frozenset({"testgate-tool"}),
            skill_categories=frozenset(),
            import_package=None,
            default_enabled=False,
        )
        patched_registry = {**FEATURE_REGISTRY, "testgate": fake_def}
        monkeypatch.setattr(ff, "FEATURE_REGISTRY", patched_registry)

        result = _collect_disabled_feature_tags({})
        assert "testgate-tool" in result

    def test_union_model_tag_claimed_by_enabled_feature(self, monkeypatch):
        from autoskillit.core import feature_flags as ff
        from autoskillit.core._type_constants import FEATURE_REGISTRY, FeatureDef, FeatureLifecycle
        from autoskillit.core.feature_flags import _collect_disabled_feature_tags

        shared_tag = frozenset({"shared-tag"})
        def_a = FeatureDef(
            lifecycle=FeatureLifecycle.EXPERIMENTAL,
            description="A",
            tool_tags=shared_tag,
            skill_categories=frozenset(),
            import_package=None,
            default_enabled=False,
        )
        def_b = FeatureDef(
            lifecycle=FeatureLifecycle.EXPERIMENTAL,
            description="B",
            tool_tags=shared_tag,
            skill_categories=frozenset(),
            import_package=None,
            default_enabled=True,
        )
        patched = {**FEATURE_REGISTRY, "feat_a": def_a, "feat_b": def_b}
        monkeypatch.setattr(ff, "FEATURE_REGISTRY", patched)

        result = _collect_disabled_feature_tags({"feat_a": False, "feat_b": True})
        # feat_b enabled claims the tag → must not appear in result
        assert "shared-tag" not in result
