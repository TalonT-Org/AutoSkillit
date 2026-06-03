"""Stop terminals must emit the correct success direction based on their graph position."""

from __future__ import annotations

import json

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

    Uses name-based detection only: a stop whose name contains a failure
    keyword (escalate, failure, error, reject) is a failure-path stop.
    Routing analysis (on_failure edges, on_result conditions) is
    unreliable because recipes legitimately route non-critical step
    failures (e.g. post-run diagnostics) to success terminals.
    """
    del ctx
    return any(pat in step_name for pat in _FAILURE_NAME_PATTERNS)


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
