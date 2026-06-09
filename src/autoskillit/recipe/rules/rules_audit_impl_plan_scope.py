"""audit-impl plan-scope mismatch: ensure plan-path argument survives remediation loops."""

from __future__ import annotations

from autoskillit.core import Severity
from autoskillit.recipe._analysis import ValidationContext
from autoskillit.recipe._analysis_bfs import bfs_reachable
from autoskillit.recipe.contracts import resolve_skill_name
from autoskillit.recipe.registry import RuleFinding, make_finding, semantic_rule
from autoskillit.recipe.schema import RecipeStep

_PLAN_PRODUCING_STEP_NAMES = frozenset({"make_plan", "plan", "rectify"})


def _is_plan_path_argument(skill_cmd: str) -> bool:
    """Return True if skill_command uses context.plan_path as the plans argument.

    Matches the first positional argument after the skill name being ${{ context.plan_path }}.
    Excludes cases that use context.all_plan_paths (already correct) or context.group_files
    (research pattern with different semantics).
    """
    if "context.all_plan_paths" in skill_cmd:
        return False
    if "context.group_files" in skill_cmd:
        return False
    return "context.plan_path" in skill_cmd


def _is_go_edge_condition(when: str | None) -> bool:
    """Return True if a routing condition represents a GO verdict edge."""
    if when is None:
        return False
    if "result.error" in when:
        return False
    return "GO" in when and "NO GO" not in when


def _build_go_filtered_adjacency(
    ctx: ValidationContext, audit_step_name: str
) -> dict[str, set[str]]:
    """Build a forward adjacency dict with GO-verdict edges from audit_impl removed.

    ``ctx.step_graph`` is a flat ``dict[str, set[str]]`` with no edge labels, so
    GO edges cannot be filtered from it directly. This walker inspects the audit_impl
    step's ``on_result.conditions`` to identify GO routes and excludes only those
    specific edges while copying all other adjacency from ``ctx.step_graph``.
    """
    audit_step = ctx.recipe.steps.get(audit_step_name)
    go_routes_from_audit: set[str] = set()
    if (
        audit_step is not None
        and audit_step.on_result is not None
        and audit_step.on_result.conditions
    ):
        for cond in audit_step.on_result.conditions:
            if cond.route and _is_go_edge_condition(cond.when):
                go_routes_from_audit.add(cond.route)

    adjacency: dict[str, set[str]] = {}
    for src, dsts in ctx.step_graph.items():
        if src == audit_step_name:
            adjacency[src] = dsts - go_routes_from_audit
        else:
            adjacency[src] = set(dsts)
    return adjacency


def _step_captures_plan_path(step: RecipeStep) -> bool:
    if "plan_path" in (step.capture or {}):
        return True
    for cap_expr in (step.capture_list or {}).values():
        if "result.plan_path" in cap_expr.from_:
            return True
    return False


def _is_plan_producing_reachable(ctx: ValidationContext, audit_step_name: str) -> bool:
    """Return True if a plan-producing step is reachable from audit_impl via non-GO routes.

    Uses forward BFS over a GO-filtered adjacency to follow the remediation branch only.
    An inverted BFS would falsely flag every recipe where plan runs before audit_impl.
    """
    adjacency = _build_go_filtered_adjacency(ctx, audit_step_name)
    reachable = bfs_reachable(adjacency, audit_step_name)
    for step_name in reachable:
        if step_name not in _PLAN_PRODUCING_STEP_NAMES:
            continue
        step = ctx.recipe.steps.get(step_name)
        if step is not None and _step_captures_plan_path(step):
            return True
    return False


@semantic_rule(
    name="audit-impl-plan-scope-mismatch",
    description=(
        "An audit-impl step that receives a plan path as the first positional argument "
        "must use context.all_plan_paths (not context.plan_path) when a remediation loop "
        "re-enters a plan step. On loop re-entry, context.plan_path is overwritten with "
        "only the remediation plan, causing the audit diff to span more changes than "
        "the plan covers."
    ),
    severity=Severity.ERROR,
)
def _check_audit_impl_plan_scope(ctx: ValidationContext) -> list[RuleFinding]:
    findings: list[RuleFinding] = []
    for step_name, step in ctx.recipe.steps.items():
        if step.tool != "run_skill":
            continue
        skill = resolve_skill_name(step.with_args.get("skill_command", ""))
        if skill != "audit-impl":
            continue
        skill_cmd = step.with_args.get("skill_command", "")
        if not _is_plan_path_argument(skill_cmd):
            continue
        if not _is_plan_producing_reachable(ctx, step_name):
            continue
        findings.append(
            make_finding(
                rule_name="audit-impl-plan-scope-mismatch",
                step_name=step_name,
                message=(
                    f"Step '{step_name}' invokes audit-impl with context.plan_path but has a "
                    f"remediation loop that re-enters a plan step via non-GO routes. On loop "
                    f"re-entry, context.plan_path is overwritten with only the remediation plan, "
                    f"causing the audit diff to span more changes than the plan covers. Use "
                    f"context.all_plan_paths instead, and ensure plan-producing steps capture "
                    f"all_plan_paths with accumulation semantics."
                ),
            )
        )
    return findings
