import pytest

pytestmark = [pytest.mark.layer("recipe"), pytest.mark.small]


def test_ingredients_importable_from_submodule():
    from autoskillit.recipe._recipe_ingredients import (
        format_ingredients_table,
    )

    assert callable(format_ingredients_table)


def test_composition_importable_from_submodule():
    from autoskillit.recipe._recipe_composition import _build_active_recipe

    assert callable(_build_active_recipe)


def test_analysis_graph_importable():
    from autoskillit.recipe._analysis_graph import (
        RouteEdge,
    )

    assert RouteEdge is not None


def test_analysis_bfs_importable():
    from autoskillit.recipe._analysis_bfs import bfs_reachable

    assert callable(bfs_reachable)


def test_analysis_detectors_importable():
    from autoskillit.recipe._analysis_detectors import (
        _detect_dead_outputs,
    )

    assert callable(_detect_dead_outputs)


def test_analysis_blocks_importable():
    from autoskillit.recipe._analysis_blocks import extract_blocks

    assert callable(extract_blocks)


def test_recipe_init_surface_unchanged():
    # ValidationContext, build_recipe_graph, bfs_reachable are NOT in recipe/__init__.py
    # — they live in _analysis.py and are consumed directly by rule modules.
    from autoskillit.recipe import (
        make_validation_context,
    )

    assert callable(make_validation_context)
