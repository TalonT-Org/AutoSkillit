"""Shared DAG operations: topological sort, SCC detection, cycle breaking."""

from __future__ import annotations

from collections import deque
from typing import Any

import networkx as nx


def topological_sort(wp_results: dict[str, dict[str, Any]]) -> list[str]:
    """Return topologically sorted WP IDs. Raises RuntimeError on cycle."""
    in_degree: dict[str, int] = {wp_id: 0 for wp_id in wp_results}
    adjacency: dict[str, list[str]] = {wp_id: [] for wp_id in wp_results}
    for wp_id, wp in wp_results.items():
        for dep in wp.get("depends_on", []):
            if dep in wp_results:
                adjacency[dep].append(wp_id)
                in_degree[wp_id] += 1

    queue: deque[str] = deque(sorted(k for k, v in in_degree.items() if v == 0))
    order: list[str] = []
    while queue:
        node = queue.popleft()
        order.append(node)
        for neighbor in sorted(adjacency[node]):
            in_degree[neighbor] -= 1
            if in_degree[neighbor] == 0:
                queue.append(neighbor)

    if len(order) < len(wp_results):
        cycle_nodes = [n for n in wp_results if n not in set(order)]
        raise RuntimeError(f"Cycle detected among WPs: {', '.join(sorted(cycle_nodes))}")
    return order


def find_sccs(adjacency: dict[str, list[str]]) -> list[set[str]]:
    """Return all SCCs with size >= 2 using Tarjan's algorithm via NetworkX."""
    graph: nx.DiGraph = nx.DiGraph()
    for node, neighbors in adjacency.items():
        graph.add_node(node)
        for neighbor in neighbors:
            graph.add_edge(node, neighbor)
    sccs: list[set[str]] = [
        scc for scc in nx.strongly_connected_components(graph) if len(scc) >= 2
    ]
    return sccs


def _build_graph(
    output_wps: list[dict[str, Any]],
) -> tuple[dict[str, list[str]], dict[str, set[str]]]:
    """Build adjacency dict and in-neighbor set from output_wps depends_on."""
    adjacency: dict[str, list[str]] = {wp["id"]: [] for wp in output_wps}
    incoming: dict[str, set[str]] = {wp["id"]: set() for wp in output_wps}
    for wp in output_wps:
        for dep in wp.get("depends_on", []):
            if dep in adjacency:
                adjacency[dep].append(wp["id"])
                incoming[wp["id"]].add(dep)
    return adjacency, incoming


def break_cycles_greedy_fas(output_wps: list[dict[str, Any]]) -> list[tuple[str, str]]:
    """Break all cycles in output_wps using greedy FAS. Mutates depends_on in-place.

    Removes the back-edge from the lexicographically-highest node in each SCC.
    Iterates until no SCC with size >= 2 remains (handles overlapping cycles).
    Returns list of (source, target) broken edges.
    """
    broken_edges: list[tuple[str, str]] = []
    wp_by_id: dict[str, dict[str, Any]] = {wp["id"]: wp for wp in output_wps}

    while True:
        adjacency, incoming = _build_graph(output_wps)
        sccs = find_sccs(adjacency)
        if not sccs:
            break

        for scc in sccs:
            scc_list = sorted(scc)
            for node in reversed(scc_list):
                for dep in sorted(incoming[node]):
                    if dep in scc and dep in wp_by_id[node].get("depends_on", []):
                        wp_by_id[node]["depends_on"].remove(dep)
                        broken_edges.append((node, dep))
                        break
                else:
                    continue
                break

    return broken_edges


def filter_self_references(output_wps: list[dict[str, Any]]) -> int:
    """Remove self-loops from depends_on. Returns count of removed self-refs."""
    count = 0
    for wp in output_wps:
        filtered = [dep for dep in wp.get("depends_on", []) if dep != wp["id"]]
        removed = len(wp.get("depends_on", [])) - len(filtered)
        if removed:
            wp["depends_on"] = filtered
            count += removed
    return count
