"""Semantic validation rules — loop counter scope isolation."""

from __future__ import annotations

import regex as _re

from autoskillit.core import Severity
from autoskillit.recipe._analysis import ValidationContext, bfs_reachable
from autoskillit.recipe._analysis_graph import _extract_routing_edges
from autoskillit.recipe._rule_helpers import _build_graph_without_nodes
from autoskillit.recipe.registry import RuleFinding, make_finding, semantic_rule

_CTX_VAR_RE = _re.compile(r"\$\{\{\s*context\.(\w+)\s*\}\}")


def _build_yaml_predecessor_map(ctx: ValidationContext) -> dict[str, set[str]]:
    preds: dict[str, set[str]] = {}
    for name, step in ctx.recipe.steps.items():
        for edge in _extract_routing_edges(step):
            if edge.target in ctx.recipe.steps:
                preds.setdefault(edge.target, set()).add(name)
    return preds


def _has_disconnected_preds(
    preds: set[str],
    modified_graph: dict[str, set[str]],
) -> tuple[str, str] | None:
    """Check if any two predecessors are mutually unreachable in the modified graph."""
    preds_list = sorted(preds)
    if len(preds_list) < 2:
        return None
    p1 = preds_list[0]
    reachable_from_p1 = bfs_reachable(modified_graph, p1) | {p1}
    for p2 in preds_list[1:]:
        if p2 not in reachable_from_p1:
            reachable_from_p2 = bfs_reachable(modified_graph, p2) | {p2}
            if p1 not in reachable_from_p2:
                return (p1, p2)
    return None


@semantic_rule(
    name="loop-counter-cross-path-sharing",
    description=(
        "A check_loop_iteration guard is reachable from two structurally "
        "disconnected entry paths that share the same counter variable"
    ),
    severity=Severity.ERROR,
)
def _check_loop_counter_cross_path_sharing(ctx: ValidationContext) -> list[RuleFinding]:
    findings: list[RuleFinding] = []
    recipe = ctx.recipe
    graph = ctx.step_graph

    yaml_preds = _build_yaml_predecessor_map(ctx)

    for step_name, step in recipe.steps.items():
        if step.tool != "run_python":
            continue
        if step.with_args.get("callable") != "autoskillit.smoke_utils.check_loop_iteration":
            continue

        current_iter_expr = step.with_args.get("current_iteration", "")
        m = _CTX_VAR_RE.search(current_iter_expr)
        if not m:
            continue
        counter_var = m.group(1)

        if counter_var not in step.capture:
            continue

        forward = bfs_reachable(graph, step_name)
        backward = bfs_reachable(yaml_preds, step_name)
        full_cycle = frozenset((forward & backward) | {step_name})

        if len(full_cycle) < 3 or len(full_cycle) > 10:
            continue

        has_test_step = any(
            (s := recipe.steps.get(sn)) is not None and s.tool == "test_check" for sn in full_cycle
        )
        if not has_test_step:
            continue

        modified_graph = _build_graph_without_nodes(graph, full_cycle)

        guard_steps = {
            sn
            for sn, s in recipe.steps.items()
            if s.tool == "run_python"
            and s.with_args.get("callable") == "autoskillit.smoke_utils.check_loop_iteration"
        }

        external_preds: dict[str, set[str]] = {}
        for member in full_cycle:
            member_step = recipe.steps.get(member)
            if member_step and member_step.tool == "test_check":
                continue
            for pred in yaml_preds.get(member, set()):
                if pred not in full_cycle and pred not in guard_steps:
                    external_preds.setdefault(member, set()).add(pred)

        for member in list(external_preds):
            external_preds[member] = {
                p
                for p in external_preds[member]
                if not (yaml_preds.get(p, set()) and yaml_preds[p] <= full_cycle)
            }
        external_preds = {k: v for k, v in external_preds.items() if v}

        all_ext = {p for ps in external_preds.values() for p in ps}
        if len(all_ext) < 2:
            continue

        pair = _has_disconnected_preds(all_ext, modified_graph)
        if pair is None:
            continue

        p1, p2 = pair
        findings.append(
            make_finding(
                rule_name="loop-counter-cross-path-sharing",
                step_name=step_name,
                message=(
                    f"Step '{step_name}' uses counter '{counter_var}' but is "
                    f"reachable from structurally disconnected entry paths: "
                    f"'{p1}' and '{p2}' cannot reach each other without "
                    f"traversing the cycle. Use separate counter variables "
                    f"for each independent failure path."
                ),
            )
        )

    return findings


@semantic_rule(
    name="loop-guard-before-verify",
    description=(
        "A check_loop_iteration guard fires before the verify step, "
        "causing the last valid fix attempt to be discarded"
    ),
    severity=Severity.WARNING,
)
def _check_loop_guard_before_verify(ctx: ValidationContext) -> list[RuleFinding]:
    findings: list[RuleFinding] = []
    recipe = ctx.recipe

    for step_name, step in recipe.steps.items():
        if step.tool != "run_python":
            continue
        if step.with_args.get("callable") != "autoskillit.smoke_utils.check_loop_iteration":
            continue

        if step.on_result is None:
            continue

        non_exit_route: str | None = None
        for cond in step.on_result.conditions:
            if cond.when and "max_exceeded" in cond.when:
                continue
            non_exit_route = cond.route
            break

        if non_exit_route is None or non_exit_route not in recipe.steps:
            continue

        verify_step = recipe.steps[non_exit_route]
        if verify_step.tool not in ("test_check", "run_skill"):
            continue

        failure_target = verify_step.on_failure
        if failure_target is None or failure_target not in recipe.steps:
            continue

        fix_step = recipe.steps[failure_target]
        fix_edges = _extract_routing_edges(fix_step)
        routes_to_guard = any(edge.target == step_name for edge in fix_edges)

        if routes_to_guard:
            findings.append(
                make_finding(
                    rule_name="loop-guard-before-verify",
                    step_name=step_name,
                    message=(
                        f"Step '{step_name}' increments the loop counter before the "
                        f"verify step '{non_exit_route}' runs (pattern: "
                        f"'{failure_target}' → '{step_name}' → '{non_exit_route}'). "
                        f"The Nth valid fix is discarded because the counter fires "
                        f"before the fix is verified. Reorder to: "
                        f"'{failure_target}' → '{non_exit_route}' → '{step_name}'."
                    ),
                )
            )

    return findings


@semantic_rule(
    name="loop-counter-not-reset-on-outer-cycle",
    description=(
        "An inner check_loop_iteration guard's counter variable is reachable "
        "from an outer check_loop_iteration guard's non-max_exceeded route "
        "without passing through a step that resets the inner counter"
    ),
    severity=Severity.WARNING,
)
def _check_loop_counter_not_reset_on_outer_cycle(ctx: ValidationContext) -> list[RuleFinding]:
    """Detect temporal counter sharing across outer audit-remediation cycles.

    When the audit-remediation outer loop re-enters the implementation
    sub-cycle, any inner guard whose counter variable is captured along that
    path (e.g. test_fix_loop_count) will accumulate across outer iterations
    unless a step resets it via autoskillit.smoke_utils.init_counter.

    Scoped to outer guards whose counter variable indicates an audit-
    remediation cycle (audit_remediation_count) — other outer/inner guard
    relationships (e.g. merge_fix wrapping merge_rebase) have separate
    reset mechanisms and are out of scope for this rule.
    """
    findings: list[RuleFinding] = []
    recipe = ctx.recipe
    graph = ctx.step_graph

    guard_steps: dict[str, str] = {}
    for step_name, step in recipe.steps.items():
        if step.tool != "run_python":
            continue
        if step.with_args.get("callable") != "autoskillit.smoke_utils.check_loop_iteration":
            continue
        current_iter_expr = step.with_args.get("current_iteration", "")
        m = _CTX_VAR_RE.search(current_iter_expr)
        if not m:
            continue
        guard_steps[step_name] = m.group(1)

    if len(guard_steps) < 2:
        return findings

    audit_outer_guards = {
        name for name, counter in guard_steps.items() if "audit_remediation" in counter
    }
    if not audit_outer_guards:
        return findings

    for inner_name, inner_counter in guard_steps.items():
        if guard_steps[inner_name] in audit_outer_guards:
            continue

        for outer_name in audit_outer_guards:
            if inner_name == outer_name:
                continue

            outer_step = recipe.steps[outer_name]
            if outer_step.on_result is None:
                continue

            non_exit_target: str | None = None
            for cond in outer_step.on_result.conditions:
                if cond.when and "max_exceeded" in cond.when:
                    continue
                non_exit_target = cond.route
                break

            if non_exit_target is None or non_exit_target not in recipe.steps:
                continue

            reachable_to_inner = bfs_reachable(graph, non_exit_target)
            if inner_name not in reachable_to_inner:
                continue

            has_reset = False
            for sn in reachable_to_inner:
                if sn == non_exit_target or sn == inner_name:
                    continue
                candidate = recipe.steps.get(sn)
                if candidate is not None and inner_counter in candidate.capture:
                    has_reset = True
                    break

            if not has_reset:
                findings.append(
                    make_finding(
                        rule_name="loop-counter-not-reset-on-outer-cycle",
                        step_name=inner_name,
                        message=(
                            f"Inner guard '{inner_name}' uses counter '{inner_counter}' "
                            f"but is reachable from audit-remediation guard "
                            f"'{outer_name}' via '{non_exit_target}' without a reset "
                            f"step. Add a step using "
                            f"'autoskillit.smoke_utils.init_counter' to capture "
                            f"'{inner_counter}' on this path so each audit-remediation "
                            f"cycle gets a fresh budget."
                        ),
                    )
                )

    return findings
