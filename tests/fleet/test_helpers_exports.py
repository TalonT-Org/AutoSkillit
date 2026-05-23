"""Tests that shared helpers are importable from tests.fleet._helpers."""

from __future__ import annotations

import pytest

pytestmark = [pytest.mark.layer("fleet"), pytest.mark.small, pytest.mark.feature("fleet")]


def test_make_recipe_info_exported_from_fleet_helpers():
    from tests.fleet._helpers import _make_recipe_info

    info = _make_recipe_info("my-recipe")
    assert info.name == "my-recipe"
    assert str(info.path) == "/fake/my-recipe.yaml"


def test_make_recipe_info_custom_prefix():
    from tests.fleet._helpers import _make_recipe_info

    info = _make_recipe_info("my-recipe", path_prefix="/fake/recipes/")
    assert str(info.path) == "/fake/recipes/my-recipe.yaml"


def test_setup_dispatch_exported_from_fleet_helpers():
    from tests.fleet._helpers import _setup_dispatch

    assert callable(_setup_dispatch)


def test_run_exported_from_fleet_helpers():
    import inspect

    from tests.fleet._helpers import _run

    assert inspect.iscoroutinefunction(_run)


def test_read_dispatch_record_exported_from_fleet_helpers():
    import inspect

    from tests.fleet._helpers import _read_dispatch_record

    sig = inspect.signature(_read_dispatch_record)
    assert "tool_ctx" in sig.parameters


def test_make_no_sentinel_exported_from_fleet_helpers():
    from tests.fleet._helpers import _make_no_sentinel

    result = _make_no_sentinel()
    assert result.outcome == "no_sentinel"


def test_make_completed_dirty_exported_from_fleet_helpers():
    from tests.fleet._helpers import _make_completed_dirty

    result = _make_completed_dirty()
    assert result.outcome == "completed_dirty"
    assert result.parse_error == "json decode error"


def test_make_completed_clean_exported_from_fleet_helpers():
    from tests.fleet._helpers import _make_completed_clean

    result = _make_completed_clean(success=True)
    assert result.outcome == "completed_clean"
    assert result.payload == {"success": True}
