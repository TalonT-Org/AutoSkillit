"""Recipe graph builders: networkx visualization and step adjacency."""

from __future__ import annotations

import dataclasses
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import networkx as nx

from autoskillit.core import get_logger
from autoskillit.recipe.schema import _TERMINAL_TARGETS, Recipe, RecipeStep

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# Infrastructure step classification (shared with diagrams rendering)
# ---------------------------------------------------------------------------


def _is_infrastructure_step(step: RecipeStep) -> bool:
    """Return True if *step* is a plumbing step that should be hidden from diagrams.

    Infrastructure steps are ``run_cmd`` steps whose sole purpose is capturing
    or setting a context value (git rev-parse, printf, echo one-liners).
    They add no user-visible behaviour to the pipeline flow.
    """
    if step.tool != "run_cmd":
        return False
    note_lower = (step.note or "").lower()
    cmd = ""
    if step.with_args and isinstance(step.with_args, dict):
        cmd = step.with_args.get("cmd", "") or ""
    return (
        "capture" in note_lower
        or "set" in note_lower
        or "printf" in cmd
        or "git rev-parse" in cmd
        or (cmd.strip().startswith("echo") and "\n" not in cmd)
    )


# ---------------------------------------------------------------------------
# networkx recipe graph builder
# ---------------------------------------------------------------------------


def build_recipe_graph(recipe: Recipe) -> nx.DiGraph:
    """Build a directed nx.DiGraph from a Recipe dataclass.

    Nodes represent recipe steps. Each vertex carries attributes matching the
    RecipeStep fields relevant to diagram rendering:
    - ``name``: step name (str)
    - ``tool``: tool identifier (str, empty string if None)
    - ``action``: action identifier (str, empty string if None)
    - ``note``: step note for semantic gate checks (str)
    - ``retries``: retry count (int)
    - ``skip_when_false``: optional condition (str, empty string if None)
    - ``is_infra``: whether the step is a hidden infrastructure step (bool)
    - ``is_terminal``: whether the step is a stop action (bool)
    - ``is_confirm``: whether the step is a confirm action (bool)

    Edges represent routing connections. Each edge carries:
    - ``edge_type``: one of ``"success"``, ``"failure"``, ``"context_limit"``,
      ``"result_condition"``, ``"exhausted"``
    - ``condition``: for ``on_result`` edges, the ``when`` expression; otherwise ``""``

    Args:
        recipe: The loaded Recipe dataclass.

    Returns:
        A directed ``nx.DiGraph`` with vertex and edge attributes as described.
    """
    import networkx as nx  # noqa: PLC0415

    step_names = list(recipe.steps.keys())
    name_to_id: dict[str, int] = {name: i for i, name in enumerate(step_names)}

    g = nx.DiGraph()
    steps_list = list(recipe.steps.values())

    g.add_nodes_from(
        (
            i,
            {
                "name": name,
                "tool": step.tool or "",
                "action": step.action or "",
                "note": step.note or "",
                "retries": step.retries,
                "skip_when_false": step.skip_when_false or "",
                "is_infra": _is_infrastructure_step(step),
                "is_terminal": step.action == "stop",
                "is_confirm": step.action == "confirm",
            },
        )
        for i, (name, step) in enumerate(zip(step_names, steps_list))
    )

    edges: list[tuple[int, int]] = []
    edge_types: list[str] = []
    edge_conditions: list[str] = []

    for name, step in recipe.steps.items():
        src = name_to_id[name]
        for edge in _extract_routing_edges(step):
            # Mirror _build_step_graph: skip on_exhausted edges for action steps
            # (stop/confirm/route steps have no retry semantics).
            if edge.edge_type == "exhausted" and step.action is not None:
                continue
            if edge.target in name_to_id:
                edges.append((src, name_to_id[edge.target]))
                edge_types.append(edge.edge_type)
                edge_conditions.append(edge.condition or "")
            elif edge.target in _TERMINAL_TARGETS:
                # Known sentinel — valid target, no graph edge needed.
                pass
            else:
                logger.warning(
                    "build_recipe_graph: step %r references unknown target %r — edge skipped",
                    name,
                    edge.target,
                )

    if edges:
        g.add_edges_from(
            (src, dst, {"edge_type": etype, "condition": cond})
            for (src, dst), etype, cond in zip(edges, edge_types, edge_conditions)
        )

    return g


# ---------------------------------------------------------------------------
# Routing edge extraction
# ---------------------------------------------------------------------------


@dataclasses.dataclass(frozen=True, slots=True)
class RouteEdge:
    """A single routing edge from a recipe step to a target step.

    Attributes:
        edge_type: One of ``"success"``, ``"failure"``, ``"context_limit"``,
            ``"result_condition"``, ``"exhausted"``.
        target: The target step name.
        condition: Populated for ``on_result`` conditions — the ``when`` expression.
        capture_available: Whether captures declared by the source step exist on this edge.
    """

    edge_type: str
    target: str
    condition: str | None = None
    capture_available: bool = False


def _extract_routing_edges(step: RecipeStep) -> list[RouteEdge]:
    """Return all routing edges declared on *step*.

    Covers every runtime routing field on :class:`RecipeStep`:
    ``on_success``, ``on_failure``, ``on_context_limit``, ``on_rate_limit``, ``on_exhausted``,
    ``on_result.conditions[].route``, and ``on_result.routes`` (dict form).

    Configuration-only ``on_skip`` and sub-recipe bypasses are added by
    :func:`_build_raw_step_edges`, not exposed as runtime routes here. None
    targets are skipped. The caller is responsible for filtering by
    known step names if graph-membership checks are needed.
    """
    edges: list[RouteEdge] = []

    if step.on_success:
        edges.append(
            RouteEdge(edge_type="success", target=step.on_success, capture_available=True)
        )
    if step.on_failure:
        edges.append(RouteEdge(edge_type="failure", target=step.on_failure))
    if step.on_context_limit:
        edges.append(RouteEdge(edge_type="context_limit", target=step.on_context_limit))
    if step.on_rate_limit:
        edges.append(RouteEdge(edge_type="rate_limit", target=step.on_rate_limit))
    if step.on_exhausted:
        edges.append(RouteEdge(edge_type="exhausted", target=step.on_exhausted))

    if step.on_result:
        sr = step.on_result
        if sr.conditions:
            for cond in sr.conditions:
                edges.append(
                    RouteEdge(
                        edge_type="result_condition",
                        target=cond.route,
                        condition=cond.when,
                        capture_available=True,
                    )
                )
        elif sr.routes:
            for key, target in sr.routes.items():
                edges.append(
                    RouteEdge(
                        edge_type="result_condition",
                        target=target,
                        condition=key,
                        capture_available=True,
                    )
                )

    return edges


# ---------------------------------------------------------------------------
# Step routing graph
# ---------------------------------------------------------------------------


def _build_raw_step_edges(recipe: Recipe) -> dict[str, tuple[RouteEdge, ...]]:
    """Build typed runtime and configuration-time edges for every recipe step."""
    step_names = set(recipe.steps)
    edges_by_source: dict[str, list[RouteEdge]] = {name: [] for name in step_names}

    for name, step in recipe.steps.items():
        for edge in _extract_routing_edges(step):
            if edge.edge_type == "exhausted" and step.action is not None:
                continue
            if edge.target in step_names:
                edges_by_source[name].append(edge)
        if step.skip_when_false and step.on_skip in step_names:
            edges_by_source[name].append(
                RouteEdge(edge_type="configuration_skip", target=step.on_skip)
            )

    predecessors: dict[str, set[str]] = {name: set() for name in step_names}
    for source, edges in edges_by_source.items():
        for edge in edges:
            predecessors[edge.target].add(source)

    for name, step in recipe.steps.items():
        if not step.skip_when_false or step.on_skip not in step_names:
            continue
        for predecessor in sorted(predecessors[name]):
            edges_by_source[predecessor].append(
                RouteEdge(
                    edge_type="configuration_skip_bypass",
                    target=step.on_skip,
                )
            )

    ordered_names = list(recipe.steps)
    for index, (name, step) in enumerate(recipe.steps.items()):
        if step.sub_recipe is None or index + 1 >= len(ordered_names):
            continue
        next_step = ordered_names[index + 1]
        edges_by_source[name].append(
            RouteEdge(edge_type="configuration_sub_recipe", target=next_step)
        )
        for predecessor in sorted(predecessors[name]):
            edges_by_source[predecessor].append(
                RouteEdge(
                    edge_type="configuration_sub_recipe_bypass",
                    target=next_step,
                )
            )

    return {source: tuple(edges) for source, edges in edges_by_source.items()}


def _build_step_graph(recipe: Recipe) -> dict[str, set[str]]:
    """Project typed raw edges into a routing adjacency list.

    Each key is a step name, each value is the set of step names
    reachable in one hop (successors). Terminal targets like "done"
    are excluded since they are not real steps.
    """
    return {
        source: {edge.target for edge in edges}
        for source, edges in _build_raw_step_edges(recipe).items()
    }
