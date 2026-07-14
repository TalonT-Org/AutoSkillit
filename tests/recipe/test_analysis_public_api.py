"""Tests for public API surface of recipe._analysis (Finding 6)."""

from __future__ import annotations

import pytest

pytestmark = [pytest.mark.layer("recipe"), pytest.mark.small]


# ---------------------------------------------------------------------------
# T4: bfs_reachable is a public symbol (Finding 6)
# ---------------------------------------------------------------------------


def test_bfs_reachable_is_public() -> None:
    """bfs_reachable must be importable from autoskillit.recipe._analysis."""
    from autoskillit.recipe._analysis import bfs_reachable  # must not raise ImportError

    assert callable(bfs_reachable)


def test_bfs_reachable_private_name_gone() -> None:
    """_bfs_reachable private name must be absent after promotion to public."""
    import autoskillit.recipe._analysis as mod

    assert not hasattr(mod, "_bfs_reachable"), (
        "_bfs_reachable private name must be removed after promotion to bfs_reachable"
    )


def test_bfs_reachable_traverses_graph() -> None:
    """bfs_reachable returns all reachable nodes from start, excluding start itself."""
    from autoskillit.recipe._analysis import bfs_reachable

    graph = {
        "a": {"b", "c"},
        "b": {"d"},
        "c": set(),
        "d": set(),
    }
    assert bfs_reachable(graph, "a") == {"b", "c", "d"}
    assert bfs_reachable(graph, "b") == {"d"}
    assert bfs_reachable(graph, "d") == set()


def test_build_success_step_graph_is_available_from_analysis_facade() -> None:
    """Rules can build success graphs without importing analysis internals."""
    from autoskillit.recipe._analysis import _build_success_step_graph

    assert callable(_build_success_step_graph)


# ---------------------------------------------------------------------------
# ValidationContext.skill_resolver tests
# ---------------------------------------------------------------------------


def test_validation_context_skill_resolver_default() -> None:
    """ValidationContext.skill_resolver defaults to None."""
    from autoskillit.recipe._analysis import ValidationContext
    from autoskillit.recipe.schema import DataFlowReport, Recipe

    recipe = Recipe(name="t", description="t", steps={}, kitchen_rules=["t"])
    ctx = ValidationContext(
        recipe=recipe,
        step_graph={},
        dataflow=DataFlowReport(warnings=[], summary=""),
    )
    assert ctx.skill_resolver is None


def test_make_validation_context_passes_skill_resolver() -> None:
    """make_validation_context sets skill_resolver on the returned context."""
    from unittest.mock import Mock

    from autoskillit.recipe._analysis import make_validation_context
    from autoskillit.recipe.schema import Recipe

    recipe = Recipe(name="t", description="t", steps={}, kitchen_rules=["t"])
    resolver = Mock()
    ctx = make_validation_context(recipe, skill_resolver=resolver)
    assert ctx.skill_resolver is resolver
