"""Semantic rules for merge queue PR state routing."""

from __future__ import annotations

import regex as re

from autoskillit.core import PRState, Severity
from autoskillit.recipe._analysis import ValidationContext
from autoskillit.recipe.registry import RuleFinding, semantic_rule

# ---------------------------------------------------------------------------
# wait_for_merge_queue routing rules (I7 + I8)
# ---------------------------------------------------------------------------

_REQUIRED_MQ_PR_STATES: frozenset[str] = frozenset(
    s.value for s in PRState if s not in {PRState.ERROR, PRState.NOT_ENROLLED}
)
_PR_STATE_WHEN_RE = re.compile(r"\$\{\{\s*result\.pr_state\s*\}\}\s*==\s*(\w+)")
_MQ_EXPECTED_FALLBACK = "register_clone_unconfirmed"


def _extract_mq_when_values(on_result: object) -> set[str]:
    """Return the set of pr_state values covered by explicit when conditions."""
    values: set[str] = set()
    for cond in getattr(on_result, "conditions", []):
        if getattr(cond, "when", None) is None:
            continue
        m = _PR_STATE_WHEN_RE.search(cond.when)
        if m:
            values.add(m.group(1))
    return values


def _recipe_has_mq_routing_step(ctx: ValidationContext) -> bool:
    """Return True if any step uses wait_for_merge_queue with predicate on_result routing."""
    return any(
        step.tool == "wait_for_merge_queue"
        and step.on_result is not None
        and getattr(step.on_result, "conditions", None)
        for step in ctx.recipe.steps.values()
    )


def _recipe_uses_register_clone_unconfirmed(ctx: ValidationContext) -> bool:
    """Return True if this recipe family uses register_clone_unconfirmed as timeout escalation.

    Used only by Rule I8 (conformance targets) — implementation/remediation-family recipes
    that define register_clone_unconfirmed must route fallback and on_failure there.  Other
    recipe families (e.g. merge-prs.yaml) route queue timeouts/errors differently and are
    exempt from target-specific conformance checks, but NOT from PRState completeness (I7).
    """
    return _MQ_EXPECTED_FALLBACK in ctx.recipe.steps


@semantic_rule(
    name="wait-for-merge-queue-routing-covers-all-pr-states",
    description=(
        "Every non-error PRState value must have an explicit when arm in "
        "wait_for_merge_queue on_result; prevents silent routing of new states to fallback"
    ),
    severity=Severity.ERROR,
)
def _check_wait_for_merge_queue_routing_covers_all_pr_states(
    ctx: ValidationContext,
) -> list[RuleFinding]:
    if not _recipe_has_mq_routing_step(ctx):
        return []
    findings: list[RuleFinding] = []
    for step_name, step in ctx.recipe.steps.items():
        if step.tool != "wait_for_merge_queue":
            continue
        if step.on_result is None or not step.on_result.conditions:
            continue
        covered = _extract_mq_when_values(step.on_result)
        missing = _REQUIRED_MQ_PR_STATES - covered
        if missing:
            findings.append(
                RuleFinding(
                    rule="wait-for-merge-queue-routing-covers-all-pr-states",
                    severity=Severity.ERROR,
                    step_name=step_name,
                    message=(
                        f"Step {step_name!r} is missing explicit routing arms for "
                        f"PRState values: {sorted(missing)}. Every non-error PRState "
                        f"must have an explicit when condition in on_result."
                    ),
                )
            )
    return findings


@semantic_rule(
    name="wait-for-merge-queue-routing-conforms-to-expected-targets",
    description=(
        "wait_for_merge_queue fallback and on_failure must both target "
        "register_clone_unconfirmed; prevents silent success routing on timeout/unknown states"
    ),
    severity=Severity.ERROR,
)
def _check_wait_for_merge_queue_routing_conforms_to_expected_targets(
    ctx: ValidationContext,
) -> list[RuleFinding]:
    if not _recipe_uses_register_clone_unconfirmed(ctx):
        return []
    findings: list[RuleFinding] = []
    for step_name, step in ctx.recipe.steps.items():
        if step.tool != "wait_for_merge_queue":
            continue
        # Check fallback condition (when=None)
        if step.on_result is not None and step.on_result.conditions:
            fallback_routes = [
                c.route for c in step.on_result.conditions if getattr(c, "when", None) is None
            ]
            for route in fallback_routes:
                if route != _MQ_EXPECTED_FALLBACK:
                    findings.append(
                        RuleFinding(
                            rule="wait-for-merge-queue-routing-conforms-to-expected-targets",
                            severity=Severity.ERROR,
                            step_name=step_name,
                            message=(
                                f"Step {step_name!r} has fallback route {route!r} but "
                                f"expected {_MQ_EXPECTED_FALLBACK!r}. The fallback must "
                                f"route to register_clone_unconfirmed so unrecognised states "
                                f"are escalated, not silently treated as success."
                            ),
                        )
                    )
        # Check on_failure
        if step.on_failure is not None and step.on_failure != _MQ_EXPECTED_FALLBACK:
            findings.append(
                RuleFinding(
                    rule="wait-for-merge-queue-routing-conforms-to-expected-targets",
                    severity=Severity.ERROR,
                    step_name=step_name,
                    message=(
                        f"Step {step_name!r} has on_failure={step.on_failure!r} but "
                        f"expected {_MQ_EXPECTED_FALLBACK!r}. Tool errors must route "
                        f"to register_clone_unconfirmed, not a success-path step."
                    ),
                )
            )
    return findings
