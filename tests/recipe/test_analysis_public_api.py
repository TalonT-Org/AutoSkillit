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
