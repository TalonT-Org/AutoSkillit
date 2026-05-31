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


# ---------------------------------------------------------------------------
# dropped_merge_group_ci unguarded re-enqueue loop (I9)
# ---------------------------------------------------------------------------


@semantic_rule(
    name="dropped-merge-group-ci-unguarded-reenqueue-loop",
    description=(
        "dropped_merge_group_ci routing must pass through a loop guard "
        "before re-entering wait_for_merge_queue"
    ),
    severity=Severity.ERROR,
)
def _check_dropped_merge_group_ci_reenqueue_guard(
    ctx: ValidationContext,
) -> list[RuleFinding]:
    if not _recipe_has_mq_routing_step(ctx):
        return []
    findings: list[RuleFinding] = []
    mq_steps_set = {n for n, s in ctx.recipe.steps.items() if s.tool == "wait_for_merge_queue"}
    for step_name, step in ctx.recipe.steps.items():
        if step.tool != "wait_for_merge_queue":
            continue
        if step.on_result is None or not step.on_result.conditions:
            continue
        max_drops = int(step.with_args.get("max_merge_group_drops", 0)) if step.with_args else 0
        if max_drops >= 1:
            continue
        dmgci_target: str | None = None
        for cond in step.on_result.conditions:
            if cond.when and "dropped_merge_group_ci" in cond.when:
                dmgci_target = cond.route
                break
        if dmgci_target is None:
            continue
        target_step = ctx.recipe.steps.get(dmgci_target)
        if target_step is not None and target_step.tool == "run_python":
            if target_step.on_result is not None:
                guard_exits: set[str] = set()
                for c in target_step.on_result.conditions or []:
                    guard_exits.add(c.route)
                if guard_exits - mq_steps_set:
                    continue
        visited_bfs: set[str] = set()
        frontier: set[str] = {dmgci_target}
        mq_reachable = False
        while frontier:
            frontier -= visited_bfs
            if not frontier:
                break
            if frontier & mq_steps_set:
                mq_reachable = True
                break
            visited_bfs |= frontier
            next_frontier: set[str] = set()
            for n in frontier:
                ns = ctx.recipe.steps.get(n)
                if ns is None:
                    continue
                if ns.on_success:
                    next_frontier.add(ns.on_success)
                if ns.on_failure:
                    next_frontier.add(ns.on_failure)
                if ns.on_result:
                    for c in ns.on_result.conditions or []:
                        next_frontier.add(c.route)
                    for v in (ns.on_result.routes or {}).values():
                        next_frontier.add(v)
            frontier = next_frontier
        if mq_reachable:
            findings.append(
                RuleFinding(
                    rule="dropped-merge-group-ci-unguarded-reenqueue-loop",
                    severity=Severity.ERROR,
                    step_name=step_name,
                    message=(
                        f"Step {step_name!r} routes dropped_merge_group_ci → "
                        f"{dmgci_target!r} without a direct loop guard. The path "
                        f"reaches wait_for_merge_queue, creating an unbounded "
                        f"re-enqueue loop. Add a run_python guard step (e.g. "
                        f"check_dropped_merge_group_ci_loop) between the "
                        f"wait_for_merge_queue and diagnose_ci steps."
                    ),
                )
            )
    return findings
