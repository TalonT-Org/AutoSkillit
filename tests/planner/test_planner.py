"""Tests for the planner L1 subpackage scaffold."""

from __future__ import annotations

import pytest

pytestmark = [pytest.mark.layer("planner"), pytest.mark.small, pytest.mark.feature("planner")]


def test_planner_package_importable() -> None:
    import autoskillit.planner  # noqa: F401


def test_planner_all_exports_callables() -> None:
    from autoskillit.planner import __all__

    assert set(__all__) == {
        "check_remaining",
        "build_assignment_manifest",
        "build_wp_manifest",
        "validate_plan",
        "compile_plan",
        "PlannerManifest",
        "PlannerManifestItem",
    }


def test_planner_feature_skill_categories() -> None:
    from autoskillit.core._type_constants import FEATURE_REGISTRY

    assert FEATURE_REGISTRY["planner"].skill_categories == frozenset({"planner"})
