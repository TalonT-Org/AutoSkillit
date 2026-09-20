"""Validate executable audit-cycle producer/consumer bindings."""

from __future__ import annotations

from autoskillit.core import Severity
from autoskillit.recipe._analysis import ValidationContext
from autoskillit.recipe._analysis_bfs import all_paths_cross, bfs_reachable
from autoskillit.recipe._skill_helpers import bound_skill_name
from autoskillit.recipe.registry import RuleFinding, make_finding, semantic_rule
from autoskillit.recipe.schema import RecipeStep


def _has_bound_input(ctx: ValidationContext, step_name: str, name: str) -> bool:
    invocation = ctx.binding_projection.for_step(step_name)
    if invocation is None:
        return False
    value = invocation.skill_input(name)
    return value is not None and value.is_present


def _no_go_routes(step: RecipeStep) -> tuple[str, ...]:
    if step.on_result is None:
        return ()
    explicit = tuple(
        condition.route
        for condition in step.on_result.conditions
        if condition.when is not None and "NO GO" in str(condition.when)
    )
    if explicit:
        return explicit
    has_go_route = any(
        condition.when is not None and "GO" in str(condition.when)
        for condition in step.on_result.conditions
    )
    if not has_go_route:
        return ()
    return tuple(
        condition.route for condition in step.on_result.conditions if condition.when is None
    )


def _no_go_route_findings(
    ctx: ValidationContext,
    audit_step_name: str,
    no_go_start: str,
    audit_steps: list[str],
    make_plan_steps: set[str],
    reported: set[tuple[str, str]],
) -> list[RuleFinding]:
    """Validate one audit NO GO route's producer, dominance, and successor bindings."""
    findings: list[RuleFinding] = []
    if no_go_start == "merge_audit_cycle_path":
        merge_step = ctx.recipe.steps.get(no_go_start)
        if merge_step is not None:
            no_go_routes = _no_go_routes(merge_step)
            if len(set(no_go_routes)) == 1:
                no_go_start = no_go_routes[0]
    reachable = bfs_reachable(ctx.step_graph, no_go_start) | {no_go_start}
    reachable_planners = sorted(make_plan_steps & reachable)
    if not reachable_planners:
        key = (audit_step_name, "producer")
        if key not in reported:
            reported.add(key)
            findings.append(
                make_finding(
                    rule_name="inventory-gate-not-bilateral",
                    step_name=audit_step_name,
                    message=(
                        f"NO GO route from '{audit_step_name}' reaches no "
                        "make-plan producer for a plan disposition report."
                    ),
                )
            )
        return findings
    for planner_name in reachable_planners:
        missing_planner = [
            name for name in ("audit_cycle_path",) if not _has_bound_input(ctx, planner_name, name)
        ]
        if "plan_disposition_path" not in ctx.recipe.steps[planner_name].capture:
            missing_planner.append("capture.plan_disposition_path")
        key = (planner_name, "producer")
        if missing_planner and key not in reported:
            reported.add(key)
            findings.append(
                make_finding(
                    rule_name="inventory-gate-not-bilateral",
                    step_name=planner_name,
                    message=(
                        f"Step '{planner_name}' is on an audit NO GO route "
                        f"but lacks executable producer bindings {missing_planner!r}."
                    ),
                )
            )
    for dry_name in sorted(
        name
        for name in reachable
        if name in ctx.recipe.steps and bound_skill_name(ctx, name) == "dry-walkthrough"
    ):
        key = (dry_name, "dominance")
        if (
            any(
                all_paths_cross(ctx.step_graph, no_go_start, planner_name, dry_name)
                for planner_name in reachable_planners
            )
            or key in reported
        ):
            continue
        reported.add(key)
        findings.append(
            make_finding(
                rule_name="inventory-gate-not-bilateral",
                step_name=dry_name,
                message=(
                    f"Dry step '{dry_name}' is reachable from audit NO GO "
                    "without crossing a make-plan disposition producer."
                ),
            )
        )
    successor_audits = (set(audit_steps) & reachable) - {audit_step_name}
    if audit_step_name in reachable and audit_step_name != no_go_start:
        successor_audits.add(audit_step_name)
    key = (audit_step_name, "successor")
    if not successor_audits and key not in reported:
        reported.add(key)
        findings.append(
            make_finding(
                rule_name="inventory-gate-not-bilateral",
                step_name=audit_step_name,
                message=(
                    f"NO GO route from '{audit_step_name}' cannot reach a "
                    "successor audit-impl verdict; remediation could not "
                    "close the active authority."
                ),
            )
        )
    for successor_name in sorted(successor_audits):
        key = (successor_name, "successor-input")
        if _has_bound_input(ctx, successor_name, "prior_audit_cycle_path") or key in reported:
            continue
        reported.add(key)
        findings.append(
            make_finding(
                rule_name="inventory-gate-not-bilateral",
                step_name=successor_name,
                message=(
                    f"Successor audit step '{successor_name}' does not consume "
                    "the bound prior_audit_cycle_path."
                ),
            )
        )
    return findings


@semantic_rule(
    name="inventory-gate-not-bilateral",
    description=(
        "Audit remediation recipes must capture immutable audit authority and deliver "
        "the exact authority/disposition tuple through compiled dry-walkthrough inputs."
    ),
    severity=Severity.ERROR,
)
def _check_inventory_gate_bilateral(ctx: ValidationContext) -> list[RuleFinding]:
    audit_steps = [
        name for name in ctx.recipe.steps if bound_skill_name(ctx, name) == "audit-impl"
    ]
    if not audit_steps:
        return []

    findings: list[RuleFinding] = []
    for audit_step_name in audit_steps:
        if "audit_cycle_path_raw" not in ctx.recipe.steps[audit_step_name].capture:
            findings.append(
                make_finding(
                    rule_name="inventory-gate-not-bilateral",
                    step_name=audit_step_name,
                    message=(
                        f"Step '{audit_step_name}' invokes audit-impl but does not "
                        "capture audit_cycle_path_raw for both verdicts."
                    ),
                )
            )

    make_plan_steps = {
        name for name in ctx.recipe.steps if bound_skill_name(ctx, name) == "make-plan"
    }
    disposition_produced = any(
        "plan_disposition_path" in ctx.recipe.steps[name].capture for name in make_plan_steps
    )
    for step_name in ctx.recipe.steps:
        if bound_skill_name(ctx, step_name) != "dry-walkthrough":
            continue
        missing = [
            name
            for name in ("audit_cycle_path", "plan_disposition_path")
            if (name == "audit_cycle_path" or disposition_produced)
            and not _has_bound_input(ctx, step_name, name)
        ]
        if not missing:
            continue
        findings.append(
            make_finding(
                rule_name="inventory-gate-not-bilateral",
                step_name=step_name,
                message=(
                    f"Step '{step_name}' invokes dry-walkthrough without compiled "
                    f"inputs {missing!r}; audit-cycle admission cannot verify the "
                    "producer authority and disposition tuple."
                ),
            )
        )

    reported: set[tuple[str, str]] = set()
    for audit_step_name in audit_steps:
        for no_go_start in _no_go_routes(ctx.recipe.steps[audit_step_name]):
            findings.extend(
                _no_go_route_findings(
                    ctx,
                    audit_step_name,
                    no_go_start,
                    audit_steps,
                    make_plan_steps,
                    reported,
                )
            )
    return findings
