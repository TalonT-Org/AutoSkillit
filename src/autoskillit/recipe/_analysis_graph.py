"""Recipe graph builders: igraph visualization and step adjacency."""

from __future__ import annotations

import dataclasses

import igraph

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
# igraph recipe graph builder
# ---------------------------------------------------------------------------


def build_recipe_graph(recipe: Recipe) -> igraph.Graph:
    """Build a directed igraph.Graph from a Recipe dataclass.

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
        A directed ``igraph.Graph`` with vertex and edge attributes as described.
    """
    step_names = list(recipe.steps.keys())
    name_to_id: dict[str, int] = {name: i for i, name in enumerate(step_names)}

    g = igraph.Graph(n=len(step_names), directed=True)
    steps_list = list(recipe.steps.values())

    g.vs["name"] = step_names
    g.vs["tool"] = [s.tool or "" for s in steps_list]
    g.vs["action"] = [s.action or "" for s in steps_list]
    g.vs["note"] = [s.note or "" for s in steps_list]
    g.vs["retries"] = [s.retries for s in steps_list]
    g.vs["skip_when_false"] = [s.skip_when_false or "" for s in steps_list]
    g.vs["is_infra"] = [_is_infrastructure_step(s) for s in steps_list]
    g.vs["is_terminal"] = [s.action == "stop" for s in steps_list]
    g.vs["is_confirm"] = [s.action == "confirm" for s in steps_list]

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
        g.add_edges(edges, attributes={"edge_type": edge_types, "condition": edge_conditions})

    return g


# ---------------------------------------------------------------------------
# Routing edge extraction
# ---------------------------------------------------------------------------


@dataclasses.dataclass
class RouteEdge:
    """A single routing edge from a recipe step to a target step.

    Attributes:
        edge_type: One of ``"success"``, ``"failure"``, ``"context_limit"``,
            ``"result_condition"``, ``"exhausted"``.
        target: The target step name.
        condition: Populated for ``on_result`` conditions — the ``when`` expression.
    """

    edge_type: str
    target: str
    condition: str | None = None


def _extract_routing_edges(step: RecipeStep) -> list[RouteEdge]:
    """Return all routing edges declared on *step*.

    Covers every routing field on :class:`RecipeStep`:
    ``on_success``, ``on_failure``, ``on_context_limit``, ``on_exhausted``,
    ``on_result.conditions[].route``, and ``on_result.routes`` (dict form).

    None targets are skipped. The caller is responsible for filtering by
    known step names if graph-membership checks are needed.
    """
    edges: list[RouteEdge] = []

    if step.on_success:
        edges.append(RouteEdge(edge_type="success", target=step.on_success))
    if step.on_failure:
        edges.append(RouteEdge(edge_type="failure", target=step.on_failure))
    if step.on_context_limit:
        edges.append(RouteEdge(edge_type="context_limit", target=step.on_context_limit))
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
                    )
                )
        elif sr.routes:
            for key, target in sr.routes.items():
                edges.append(
                    RouteEdge(
                        edge_type="result_condition",
                        target=target,
                        condition=key,
                    )
                )

    return edges


# ---------------------------------------------------------------------------
# Step routing graph
# ---------------------------------------------------------------------------


def _build_step_graph(recipe: Recipe) -> dict[str, set[str]]:
    """Build a routing adjacency list from all step routing fields.

    Each key is a step name, each value is the set of step names
    reachable in one hop (successors). Terminal targets like "done"
    are excluded since they are not real steps.
    """
    step_names = set(recipe.steps.keys())
    graph: dict[str, set[str]] = {name: set() for name in step_names}

    for name, step in recipe.steps.items():
        for edge in _extract_routing_edges(step):
            # exhausted only applies to non-terminal steps
            if edge.edge_type == "exhausted" and step.action is not None:
                continue
            if edge.target in step_names:
                graph[name].add(edge.target)

    # Build predecessor map for bypass edge injection below.
    predecessors: dict[str, set[str]] = {name: set() for name in step_names}
    for name, successors in graph.items():
        for s in successors:
            predecessors[s].add(name)

    # For each step with skip_when_false, add bypass edges from all predecessors
    # directly to the step's routing targets (the steps to route to when skipped).
    # This makes optional-step bypass paths visible to graph-based rules.
    for name, step in recipe.steps.items():
        if not step.skip_when_false:
            continue
        # on_success bypass: predecessor → step.on_success
        if step.on_success and step.on_success in step_names:
            for pred in predecessors[name]:
                graph[pred].add(step.on_success)
        # on_result bypass: predecessor → each on_result condition route target
        if step.on_result:
            if step.on_result.routes:
                for target in step.on_result.routes.values():
                    if target in step_names:
                        for pred in predecessors[name]:
                            graph[pred].add(target)
            for condition in step.on_result.conditions:
                if condition.route in step_names:
                    for pred in predecessors[name]:
                        graph[pred].add(condition.route)

    # For each sub_recipe placeholder step (gate-controlled), add a bypass edge
    # to the next step in YAML order. When the gate is false the step is dropped
    # at load time; without this edge the next step becomes unreachable in the
    # raw recipe graph, breaking reachability-based semantic rules.
    step_names_list = list(recipe.steps.keys())
    for i, (name, step) in enumerate(recipe.steps.items()):
        if step.sub_recipe is None or i + 1 >= len(step_names_list):
            continue
        next_step = step_names_list[i + 1]
        graph[name].add(next_step)
        for pred in predecessors[name]:
            graph[pred].add(next_step)

    return graph
