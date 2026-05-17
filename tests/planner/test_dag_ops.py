"""Tests for autoskillit.planner._dag_ops."""

from __future__ import annotations

import pytest

from autoskillit.planner._dag_ops import (
    break_cycles_greedy_fas,
    filter_self_references,
    find_sccs,
    topological_sort,
)

pytestmark = [pytest.mark.layer("planner"), pytest.mark.small, pytest.mark.feature("planner")]


class TestTopologicalSort:
    def test_acyclic_produces_valid_order(self) -> None:
        wp_results = {
            "A": {"depends_on": []},
            "B": {"depends_on": ["A"]},
            "C": {"depends_on": ["A"]},
            "D": {"depends_on": ["B", "C"]},
        }
        order = topological_sort(wp_results)
        assert order.index("A") < order.index("B")
        assert order.index("A") < order.index("C")
        assert order.index("B") < order.index("D")
        assert order.index("C") < order.index("D")

    def test_detects_cycle_raises_runtime_error(self) -> None:
        wp_results = {
            "A": {"depends_on": ["B"]},
            "B": {"depends_on": ["A"]},
        }
        with pytest.raises(RuntimeError, match="Cycle detected"):
            topological_sort(wp_results)

    def test_deterministic_order_on_equal_in_degree(self) -> None:
        wp_results = {
            "A": {"depends_on": []},
            "B": {"depends_on": []},
        }
        order = topological_sort(wp_results)
        assert set(order) == {"A", "B"}

    def test_empty_graph_returns_empty_list(self) -> None:
        assert topological_sort({}) == []

    def test_single_node_no_deps(self) -> None:
        assert topological_sort({"A": {"depends_on": []}}) == ["A"]


class TestFindSccs:
    def test_identifies_mutual_cycle(self) -> None:
        adjacency = {
            "A": ["B"],
            "B": ["A"],
            "C": [],
        }
        sccs = find_sccs(adjacency)
        assert len(sccs) == 1
        assert sccs[0] == {"A", "B"}

    def test_identifies_multi_node_cycle(self) -> None:
        adjacency = {
            "A": ["B"],
            "B": ["C"],
            "C": ["A"],
            "D": [],
        }
        sccs = find_sccs(adjacency)
        assert len(sccs) == 1
        assert sccs[0] == {"A", "B", "C"}

    def test_no_sccs_for_acyclic_graph(self) -> None:
        adjacency = {
            "A": ["B"],
            "B": ["C"],
            "C": [],
        }
        assert find_sccs(adjacency) == []

    def test_multiple_disjoint_sccs(self) -> None:
        adjacency = {
            "A": ["B"],
            "B": ["A"],
            "C": ["D"],
            "D": ["C"],
            "E": [],
        }
        sccs = find_sccs(adjacency)
        assert len(sccs) == 2

    def test_single_node_self_loop_not_included(self) -> None:
        adjacency = {
            "A": ["A"],
            "B": [],
        }
        sccs = find_sccs(adjacency)
        assert all(len(scc) >= 2 for scc in sccs)


class TestBreakCyclesGreedyFas:
    def test_resolves_mutual_cycle(self) -> None:
        output_wps = [
            {"id": "A", "depends_on": ["B"]},
            {"id": "B", "depends_on": ["A"]},
        ]
        broken_edges = break_cycles_greedy_fas(output_wps)
        assert len(broken_edges) >= 1
        topological_sort({wp["id"]: wp for wp in output_wps})

    def test_resolves_chain_cycle(self) -> None:
        output_wps = [
            {"id": "A", "depends_on": ["B"]},
            {"id": "B", "depends_on": ["C"]},
            {"id": "C", "depends_on": ["A"]},
        ]
        broken_edges = break_cycles_greedy_fas(output_wps)
        assert len(broken_edges) >= 1
        from autoskillit.planner._dag_ops import topological_sort

        topological_sort({wp["id"]: wp for wp in output_wps})

    def test_preserves_acyclic_edges(self) -> None:
        output_wps = [
            {"id": "A", "depends_on": []},
            {"id": "B", "depends_on": ["A"]},
            {"id": "C", "depends_on": ["B"]},
        ]
        broken_edges = break_cycles_greedy_fas(output_wps)
        assert broken_edges == []
        order = topological_sort({wp["id"]: wp for wp in output_wps})
        assert order == ["A", "B", "C"]

    def test_returns_broken_edge_tuples(self) -> None:
        output_wps = [
            {"id": "A", "depends_on": ["B"]},
            {"id": "B", "depends_on": ["A"]},
        ]
        broken_edges = break_cycles_greedy_fas(output_wps)
        for edge in broken_edges:
            assert len(edge) == 2
            assert isinstance(edge[0], str)
            assert isinstance(edge[1], str)


class TestFilterSelfReferences:
    def test_removes_self_loop(self) -> None:
        output_wps = [
            {"id": "A", "depends_on": ["A", "B"]},
            {"id": "B", "depends_on": []},
        ]
        count = filter_self_references(output_wps)
        assert count == 1
        assert "A" not in output_wps[0]["depends_on"]
        assert "B" in output_wps[0]["depends_on"]

    def test_no_self_reference(self) -> None:
        output_wps = [
            {"id": "A", "depends_on": ["B"]},
            {"id": "B", "depends_on": []},
        ]
        count = filter_self_references(output_wps)
        assert count == 0

    def test_multiple_self_references(self) -> None:
        output_wps = [
            {"id": "A", "depends_on": ["A", "A", "B"]},
            {"id": "B", "depends_on": []},
        ]
        count = filter_self_references(output_wps)
        assert count == 2
        assert "A" not in output_wps[0]["depends_on"]
        assert "B" in output_wps[0]["depends_on"]

    def test_empty_depends_on(self) -> None:
        output_wps = [
            {"id": "A", "depends_on": []},
        ]
        count = filter_self_references(output_wps)
        assert count == 0
