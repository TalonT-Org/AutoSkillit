"""Tests for the planner L1 subpackage scaffold."""

from __future__ import annotations

import pytest

pytestmark = [pytest.mark.layer("planner"), pytest.mark.small]


def test_planner_package_importable() -> None:
    import autoskillit.planner  # noqa: F401


def test_planner_all_exports_callables() -> None:
    from autoskillit.planner import __all__

    assert set(__all__) == {"check_remaining", "build_assignment_manifest", "build_wp_manifest"}
