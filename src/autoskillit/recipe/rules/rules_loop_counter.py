"""Semantic validation rules — loop counter scope isolation."""

from __future__ import annotations

import regex as _re

from autoskillit.core import Severity
from autoskillit.recipe._analysis import ValidationContext, bfs_reachable
from autoskillit.recipe._analysis_graph import _extract_routing_edges
from autoskillit.recipe._rule_helpers import _build_graph_without_nodes, _find_cycle_members
from autoskillit.recipe.registry import RuleFinding, semantic_rule

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
    all_cycles = _find_cycle_members(graph, recipe.steps)

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

        full_cycle = frozenset().union(*(c for c in all_cycles if step_name in c))
        if not full_cycle:
            continue

        if len(full_cycle) < 3:
            continue

        has_test_step = any(
            (s := recipe.steps.get(m)) is not None and s.tool == "test_check" for m in full_cycle
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
            RuleFinding(
                rule="loop-counter-cross-path-sharing",
                severity=Severity.ERROR,
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
                RuleFinding(
                    rule="loop-guard-before-verify",
                    severity=Severity.WARNING,
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
