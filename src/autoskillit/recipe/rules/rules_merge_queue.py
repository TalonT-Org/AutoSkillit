"""Semantic rules for merge queue push routing completeness."""

from __future__ import annotations

from autoskillit.core import Severity
from autoskillit.recipe._analysis import ValidationContext
from autoskillit.recipe._analysis_graph import _extract_routing_edges
from autoskillit.recipe.registry import RuleFinding, semantic_rule

_MERGE_QUEUE_WAIT_TOOLS = frozenset({"wait_for_merge_queue"})
_PUSH_TOOLS = frozenset({"push_to_remote"})


def _collect_ejection_exit_steps(ctx: ValidationContext) -> set[str]:
    """Collect step names that are ejection-route exits from merge-queue-wait steps."""
    exits: set[str] = set()
    for step_name, step in ctx.recipe.steps.items():
        if step.tool not in _MERGE_QUEUE_WAIT_TOOLS:
            continue
        if step.on_result and step.on_result.conditions:
            for cond in step.on_result.conditions:
                if cond.when and "ejected" in cond.when.lower():
                    exits.add(cond.route)
    return exits


def _bfs_forward(graph: dict[str, set[str]], starts: set[str]) -> set[str]:
    """BFS from start nodes through forward step graph."""
    reachable: set[str] = set()
    frontier = starts & set(graph)
    while frontier:
        reachable |= frontier
        next_frontier: set[str] = set()
        for name in frontier:
            next_frontier |= graph.get(name, set()) - reachable
        frontier = next_frontier
    return reachable


def _step_has_queued_branch_route(step_name: str, ctx: ValidationContext) -> bool:
    """Check if a step's failure target chain includes a queued_branch route."""
    step = ctx.recipe.steps.get(step_name)
    if not step or not step.on_failure:
        return False
    failure_target = step.on_failure
    target_step = ctx.recipe.steps.get(failure_target)
    if not target_step:
        return False
    edges = _extract_routing_edges(target_step)
    for edge in edges:
        if edge.edge_type == "result_condition" and edge.condition:
            if "queued_branch" in edge.condition:
                return True
    return False


@semantic_rule(
    name="push-after-queue-requires-queued-branch-route",
    description=(
        "A push_to_remote step reachable from a merge-queue ejection route must have "
        "its on_failure target include a queued_branch error route. Without this, a "
        "GH006 rejection from pushing to a still-protected branch causes terminal "
        "failure instead of recoverable retry."
    ),
    severity=Severity.ERROR,
)
def _check_push_after_queue_has_queued_branch_route(
    ctx: ValidationContext,
) -> list[RuleFinding]:
    ejection_exits = _collect_ejection_exit_steps(ctx)
    if not ejection_exits:
        return []

    reachable = _bfs_forward(ctx.step_graph, ejection_exits)

    findings: list[RuleFinding] = []
    for step_name in reachable:
        step = ctx.recipe.steps.get(step_name)
        if not step or step.tool not in _PUSH_TOOLS:
            continue
        if not _step_has_queued_branch_route(step_name, ctx):
            findings.append(
                RuleFinding(
                    rule="push-after-queue-requires-queued-branch-route",
                    severity=Severity.ERROR,
                    step_name=step_name,
                    message=(
                        f"Step '{step_name}' uses push_to_remote and is reachable from a "
                        f"merge-queue ejection route, but its on_failure chain does not "
                        f"include a 'queued_branch' error route. Add a classify_push_failure "
                        f"step that routes queued_branch errors to a dequeue-retry path."
                    ),
                )
            )
    return findings
