"""issue-scope-not-threaded-to-walkthrough: dry-walkthrough must receive issue context."""

from __future__ import annotations

from autoskillit.core import Severity
from autoskillit.recipe._analysis import ValidationContext
from autoskillit.recipe._delivery import analyze_step_delivery
from autoskillit.recipe.contracts import resolve_skill_name
from autoskillit.recipe.registry import RuleFinding, make_finding, semantic_rule


def _has_issue_ingredient(ctx: ValidationContext) -> bool:
    return "issue_url" in ctx.recipe.ingredients or "issue_number" in ctx.recipe.ingredients


def _threads_issue_context(step: object) -> bool:
    """True iff issue_url or issue_number appears in the step's worker-bound evidence.

    Sibling ``with:`` keys do NOT count — only references inside the
    ``skill_command`` string qualify as worker delivery.
    """
    evidence = analyze_step_delivery(
        step, optional_context_refs=getattr(step, "optional_context_refs", [])
    )
    return bool(
        "issue_url" in evidence.worker_bound_refs
        or "issue_url" in evidence.tool_bound_refs
        or "issue_number" in evidence.worker_bound_refs
        or "issue_number" in evidence.tool_bound_refs
    )


@semantic_rule(
    name="issue-scope-not-threaded-to-walkthrough",
    description=(
        "A dry-walkthrough step in a recipe with an issue_url (or issue_number) "
        "ingredient must receive issue_url (or issue_number) via its skill_command. "
        "Without this, dry-walkthrough cannot validate plan-vs-issue coverage, "
        "allowing silent descoping of issue-enumerated remediation items."
    ),
    severity=Severity.ERROR,
)
def _check_issue_scope_threading(ctx: ValidationContext) -> list[RuleFinding]:
    if not _has_issue_ingredient(ctx):
        return []

    findings: list[RuleFinding] = []
    for step_name, step in ctx.recipe.steps.items():
        if step.tool != "run_skill":
            continue
        skill = resolve_skill_name(step.with_args.get("skill_command", ""))
        if skill != "dry-walkthrough":
            continue
        if _threads_issue_context(step):
            continue
        findings.append(
            make_finding(
                rule_name="issue-scope-not-threaded-to-walkthrough",
                step_name=step_name,
                message=(
                    f"Step '{step_name}' invokes dry-walkthrough but does not thread "
                    f"issue_url (or issue_number) via its skill_command. The recipe has "
                    f"an issue ingredient but dry-walkthrough cannot fetch the issue "
                    f"body for Step 4.6 plan-vs-issue coverage validation. Place "
                    f"'${{{{ inputs.issue_url }}}}' (or issue_number) at the correct "
                    f"positional slot in the skill_command string."
                ),
            )
        )
    return findings
