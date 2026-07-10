"""inventory-gate-not-bilateral: dry-walkthrough must receive
remediation_path in audit-impl remediation loops."""

from __future__ import annotations

from autoskillit.core import Severity
from autoskillit.recipe._analysis import ValidationContext
from autoskillit.recipe._analysis_bfs import bfs_reachable
from autoskillit.recipe._delivery import analyze_step_delivery
from autoskillit.recipe.contracts import resolve_skill_name
from autoskillit.recipe.registry import RuleFinding, make_finding, semantic_rule


def _has_audit_impl_remediation_step(ctx: ValidationContext) -> bool:
    """Return True if any step invokes audit-impl and captures remediation_path."""
    for step in ctx.recipe.steps.values():
        if step.tool != "run_skill":
            continue
        skill = resolve_skill_name(step.with_args.get("skill_command", ""))
        if skill != "audit-impl":
            continue
        capture = step.capture or {}
        if "remediation_path" in capture:
            return True
    return False


def _dry_walkthrough_reachable_from_audit(
    ctx: ValidationContext, audit_step_name: str
) -> str | None:
    """Return the first dry-walkthrough step reachable from audit_impl via non-GO routes.

    Uses GO-filtered adjacency so that the GO verdict edge from audit-impl is
    excluded — only the remediation branch is traced.
    """
    from autoskillit.recipe.rules.rules_audit_impl_plan_scope import (
        _build_go_filtered_adjacency,
    )

    adjacency = _build_go_filtered_adjacency(ctx, audit_step_name)
    reachable = bfs_reachable(adjacency, audit_step_name)
    for step_name in reachable:
        step = ctx.recipe.steps.get(step_name)
        if step is None or step.tool != "run_skill":
            continue
        skill = resolve_skill_name(step.with_args.get("skill_command", ""))
        if skill == "dry-walkthrough":
            return step_name
    return None


def _threads_remediation_path(step: object) -> bool:
    """True iff remediation_path appears in the step's worker-bound evidence.

    Sibling ``with:`` keys do NOT count — only references inside the
    ``skill_command`` string qualify as worker delivery.
    """
    evidence = analyze_step_delivery(
        step, optional_context_refs=getattr(step, "optional_context_refs", [])
    )
    return (
        "remediation_path" in evidence.worker_bound_refs
        or "remediation_path" in evidence.tool_bound_refs
    )


@semantic_rule(
    name="inventory-gate-not-bilateral",
    description=(
        "A recipe with an audit-impl step that captures remediation_path and a "
        "dry-walkthrough step reachable from audit-impl via non-GO routes must "
        "thread remediation_path to dry-walkthrough via its with: block. Without "
        "this, dry-walkthrough Step 4.7 cannot distinguish satisfied from unmapped "
        "requirements in the two-disposition plan-vs-inventory gate."
    ),
    severity=Severity.ERROR,
)
def _check_inventory_gate_bilateral(ctx: ValidationContext) -> list[RuleFinding]:
    if not _has_audit_impl_remediation_step(ctx):
        return []

    findings: list[RuleFinding] = []
    for step_name, step in ctx.recipe.steps.items():
        if step.tool != "run_skill":
            continue
        skill = resolve_skill_name(step.with_args.get("skill_command", ""))
        if skill != "audit-impl":
            continue
        capture = step.capture or {}
        if "remediation_path" not in capture:
            continue

        dw_step_name = _dry_walkthrough_reachable_from_audit(ctx, step_name)
        if dw_step_name is None:
            continue

        dw_step = ctx.recipe.steps[dw_step_name]
        if _threads_remediation_path(dw_step):
            continue

        findings.append(
            make_finding(
                rule_name="inventory-gate-not-bilateral",
                step_name=dw_step_name,
                message=(
                    f"Step '{dw_step_name}' invokes dry-walkthrough but does not "
                    f"thread remediation_path via its skill_command. The recipe "
                    f"has an audit-impl step that captures remediation_path "
                    f"(step '{step_name}'), and dry-walkthrough is reachable from "
                    f"audit-impl via non-GO routes. Without remediation_path, "
                    f"dry-walkthrough Step 4.7 cannot distinguish satisfied from "
                    f"unmapped requirements in the two-disposition gate. Place "
                    f"'${{{{ context.remediation_path }}}}' at the correct "
                    f"positional slot in the skill_command string."
                ),
            )
        )
    return findings
