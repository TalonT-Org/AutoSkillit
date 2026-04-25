"""Tests for the planner L1 subpackage scaffold."""

from __future__ import annotations

import pytest

pytestmark = [pytest.mark.layer("planner"), pytest.mark.small]


def test_planner_package_importable() -> None:
    """planner package can be imported without error."""
    import autoskillit.planner  # noqa: F401


def test_planner_all_is_empty() -> None:
    """__all__ is empty — scaffold only, no public API yet."""
    from autoskillit.planner import __all__

    assert __all__ == []
