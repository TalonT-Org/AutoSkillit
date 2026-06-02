"""Stop terminals must emit the correct success direction based on their graph position."""

from __future__ import annotations

import json
from collections import deque

from autoskillit.core import Severity
from autoskillit.recipe._analysis import ValidationContext
from autoskillit.recipe._rule_helpers import (
    extract_sentinel_json_blocks,
    is_failure_stop,
    is_success_stop,
)
from autoskillit.recipe.registry import RuleFinding, semantic_rule

_FAILURE_NAME_PATTERNS = frozenset({"escalate", "failure", "error", "reject"})

RULE_NAME = "stop-sentinel-success-mismatch"


def _is_failure_path_stop(step_name: str, ctx: ValidationContext) -> bool:
    """Return True if the stop step is on a failure path.

    A stop is on a failure path if:
    (a) its name contains a failure keyword, OR
    (b) it is reachable from an on_failure edge of any step, OR
    (c) it is the target of a failure-verdict on_result condition.
    """
    if any(pat in step_name for pat in _FAILURE_NAME_PATTERNS):
        return True

    for _name, step in ctx.recipe.steps.items():
        if step.on_failure == step_name:
            return True

        if step.on_result and step.on_result.conditions:
            for cond in step.on_result.conditions:
                if cond.route != step_name:
                    continue
                if not cond.when:
                    continue
                for keyword in _FAILURE_NAME_PATTERNS:
                    if keyword in cond.when:
                        return True

    on_failure_targets: set[str] = set()
    for _name, step in ctx.recipe.steps.items():
        if step.on_failure:
            on_failure_targets.add(step.on_failure)

    reachable_from_failure: set[str] = set()
    queue: deque[str] = deque(on_failure_targets)
    while queue:
        node = queue.popleft()
        if node in reachable_from_failure:
            continue
        reachable_from_failure.add(node)
        n_step = ctx.recipe.steps.get(node)
        if n_step is None:
            continue
        if n_step.on_success:
            queue.append(n_step.on_success)
        if n_step.on_failure:
            queue.append(n_step.on_failure)
        if n_step.on_result and n_step.on_result.conditions:
            for cond in n_step.on_result.conditions:
                if cond.route:
                    queue.append(cond.route)

    return step_name in reachable_from_failure


@semantic_rule(
    name=RULE_NAME,
    description=(
        "Stop terminals must emit the correct success direction based on their graph position"
    ),
    severity=Severity.ERROR,
)
def _check_stop_sentinel_direction(ctx: ValidationContext) -> list[RuleFinding]:
    findings: list[RuleFinding] = []

    for step_name, step in ctx.recipe.steps.items():
        if step.action != "stop":
            continue
        if not step.message:
            continue

        has_sentinel = False
        for block in extract_sentinel_json_blocks(step.message):
            try:
                parsed = json.loads(block)
            except (json.JSONDecodeError, ValueError):
                continue
            if isinstance(parsed, dict) and "success" in parsed:
                has_sentinel = True
                break

        if not has_sentinel:
            continue

        on_failure_path = _is_failure_path_stop(step_name, ctx)

        if on_failure_path and is_success_stop(step):
            findings.append(
                RuleFinding(
                    rule=RULE_NAME,
                    severity=Severity.ERROR,
                    step_name=step_name,
                    message=(
                        f"Stop step '{step_name}' is on a failure path but emits "
                        f"success=true sentinel — should emit success=false"
                    ),
                )
            )
        elif not on_failure_path and is_failure_stop(step):
            findings.append(
                RuleFinding(
                    rule=RULE_NAME,
                    severity=Severity.ERROR,
                    step_name=step_name,
                    message=(
                        f"Stop step '{step_name}' is on a success path but emits "
                        f"success=false sentinel — should emit success=true"
                    ),
                )
            )

    return findings
