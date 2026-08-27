"""Semantic rule for release_issue reachability from merge-wait timeout exits (R7)."""

from __future__ import annotations

import regex as re

from autoskillit.core import Severity
from autoskillit.recipe._analysis import ValidationContext
from autoskillit.recipe.registry import RuleFinding, make_finding, semantic_rule

_TIMEOUT_CONDITION_RE = re.compile(r"timeout", re.IGNORECASE)
_MERGE_WAIT_TOOLS = frozenset({"wait_for_merge_queue"})
_DIRECT_WAIT_NAMES = re.compile(r"wait_for_(direct|immediate)_merge")
_REGISTER_CLONE_UNCONFIRMED = "register_clone_unconfirmed"


def _collect_timeout_exit_steps(ctx: ValidationContext) -> set[str]:
    """Collect step names that are timeout exits from merge-wait steps.

    A timeout exit is any step name that appears as the route of an on_result
    condition containing 'timeout' in its when-expression, where the source
    step is a merge-wait step (wait_for_merge_queue, wait_for_direct_merge,
    wait_for_immediate_merge run_cmd steps). The on_failure of any merge-wait
    step is also treated as a timeout exit (tool error is also unconfirmed).
    """
    exits: set[str] = set()
    for step_name, step in ctx.recipe.steps.items():
        is_merge_wait = step.tool in _MERGE_WAIT_TOOLS or (
            step.tool == "run_cmd" and bool(_DIRECT_WAIT_NAMES.search(step_name))
        )
        if not is_merge_wait:
            continue
        if step.on_result and step.on_result.conditions:
            for cond in step.on_result.conditions:
                if cond.when and _TIMEOUT_CONDITION_RE.search(cond.when):
                    exits.add(cond.route)
        if step.on_failure:
            exits.add(step.on_failure)
    return exits


@semantic_rule(
    name="release-issue-on-unconfirmed-merge",
    description=(
        "A release_issue step must not be reachable from a merge-wait timeout exit. "
        "When wait_for_merge_queue / wait_for_direct_merge / wait_for_immediate_merge "
        "times out, the PR is still actively in the queue. Calling release_issue removes "
        "the in-progress label, leaving the issue visually unclaimed while the merge is "
        "still pending. Route timeout exits to register_clone_unconfirmed instead."
    ),
    severity=Severity.ERROR,
)
def _check_release_issue_on_unconfirmed_merge(ctx: ValidationContext) -> list[RuleFinding]:
    timeout_exits = _collect_timeout_exit_steps(ctx)
    if not timeout_exits:
        return []

    # BFS from all timeout exits using the forward step_graph
    reachable: set[str] = set()
    frontier = set(timeout_exits) & set(ctx.step_graph)
    while frontier:
        reachable |= frontier
        next_frontier: set[str] = set()
        for name in frontier:
            next_frontier |= ctx.step_graph.get(name, set()) - reachable
        frontier = next_frontier

    findings: list[RuleFinding] = []
    for step_name in reachable:
        step = ctx.recipe.steps.get(step_name)
        if step and step.tool == "release_issue":
            findings.append(
                make_finding(
                    rule_name="release-issue-on-unconfirmed-merge",
                    step_name=step_name,
                    message=(
                        f"Step '{step_name}' calls release_issue but is reachable from a "
                        f"merge-wait timeout exit ({sorted(timeout_exits)}). "
                        f"Calling release_issue on a timeout path removes the in-progress label "
                        f"while the PR may still be queued. Replace with "
                        f"{_REGISTER_CLONE_UNCONFIRMED} (status: unconfirmed) so the label"
                        f" is kept."
                    ),
                )
            )
    return findings
