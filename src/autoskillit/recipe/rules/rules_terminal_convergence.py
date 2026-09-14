"""Semantic validation rule for success-stop reason uniqueness."""

from __future__ import annotations

from collections import defaultdict
from itertools import combinations

import regex as re

from autoskillit.core import Severity
from autoskillit.recipe._analysis import ValidationContext
from autoskillit.recipe.registry import RuleFinding, make_finding, semantic_rule
from autoskillit.recipe.schema import RecipeKind

_REASON_RE = re.compile(r'"reason"\s*:\s*"([^"]+)"')
_SUCCESS_RE = re.compile(r'"success"\s*:\s*(true|false)')


def _predecessors_bfs(step_graph: dict[str, set[str]], start: str) -> set[str]:
    """Return all ancestors of *start* — steps from which *start* is reachable."""
    reverse: dict[str, set[str]] = defaultdict(set)
    for src, dsts in step_graph.items():
        for dst in dsts:
            reverse[dst].add(src)
    visited: set[str] = {start}
    queue: list[str] = [start]
    while queue:
        node = queue.pop()
        for pred in reverse.get(node, set()):
            if pred not in visited:
                visited.add(pred)
                queue.append(pred)
    return visited


def _success_stop_reason(message: str | None) -> str | None:
    message = message or ""
    success_match = _SUCCESS_RE.search(message)
    if not success_match or success_match.group(1) != "true":
        return None
    reason_match = _REASON_RE.search(message)
    return reason_match.group(1) if reason_match else None


@semantic_rule(
    name="success-stop-reason-uniqueness",
    description=(
        "Each success=true stop step in a dispatchable recipe must have a unique "
        "reason in its sentinel example. Shared reasons make outcomes indistinguishable "
        "to the fleet outcome classifier."
    ),
    severity=Severity.WARNING,
)
def _check_success_stop_reason_uniqueness(ctx: ValidationContext) -> list[RuleFinding]:
    if ctx.recipe.kind == RecipeKind.CAMPAIGN:
        return []

    success_stops: dict[str, list[str]] = defaultdict(list)

    for step_name, step in ctx.recipe.steps.items():
        if step.action != "stop":
            continue
        reason = _success_stop_reason(step.message)
        if reason is not None:
            success_stops[reason].append(step_name)

    findings: list[RuleFinding] = []
    for reason, step_names in success_stops.items():
        if len(step_names) < 2:
            continue
        ancestors_by_step = {name: _predecessors_bfs(ctx.step_graph, name) for name in step_names}
        for name_a, name_b in combinations(step_names, 2):
            shared_ancestors = ancestors_by_step[name_a] & ancestors_by_step[name_b]
            if shared_ancestors:
                findings.append(
                    make_finding(
                        rule_name="success-stop-reason-uniqueness",
                        step_name=name_a,
                        message=(
                            f"Stop steps '{name_a}' and '{name_b}' both emit "
                            f'success=true with reason="{reason}" and share common '
                            f"ancestors. Distinct success paths must use distinct "
                            f"reason strings for fleet outcome classification."
                        ),
                        severity=Severity.ERROR,
                    )
                )
            else:
                findings.append(
                    make_finding(
                        rule_name="success-stop-reason-uniqueness",
                        step_name=name_a,
                        message=(
                            f"Stop steps '{name_a}' and '{name_b}' both emit "
                            f'success=true with reason="{reason}". Consider using '
                            f"distinct reason strings for fleet outcome "
                            f"classification."
                        ),
                        severity=Severity.WARNING,
                    )
                )

    return findings
