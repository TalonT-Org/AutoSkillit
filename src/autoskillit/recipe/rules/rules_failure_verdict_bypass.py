"""Detects when bypass routes from verdict-gated steps can reach success stop terminals.

on_context_limit and on_rate_limit bypass on_result routing entirely. If a step
declares failure verdicts via on_result but its bypass route can reach a success
stop terminal, the failure verdict is silently circumvented.
"""

from __future__ import annotations

from autoskillit.core import Severity, resolve_skill_name
from autoskillit.recipe._analysis import ValidationContext
from autoskillit.recipe._analysis_bfs import _bfs_capped
from autoskillit.recipe._rule_helpers import is_success_stop
from autoskillit.recipe._skill_helpers import get_allowed_values_for_skill
from autoskillit.recipe.registry import RuleFinding, make_finding, semantic_rule
from autoskillit.recipe.schema import RecipeStep


def _has_routed_failure_verdict(step: RecipeStep, ctx: ValidationContext) -> bool:
    """Whether a skill's declared verdict has a route to a non-success stop."""
    if not step.on_result:
        return False
    skill_command = str((step.with_args or {}).get("skill_command") or "")
    skill_name = resolve_skill_name(skill_command)
    if not skill_name:
        return False
    allowed_by_output = get_allowed_values_for_skill(skill_name)
    if not allowed_by_output:
        return False
    for _output_name, allowed_values in allowed_by_output.items():
        for value in allowed_values:
            for condition in step.on_result.conditions or []:
                if condition.when and value in condition.when and condition.route:
                    target_step = ctx.recipe.steps.get(condition.route)
                    if (
                        target_step
                        and target_step.action == "stop"
                        and not is_success_stop(target_step)
                    ):
                        return True
    return False


@semantic_rule(
    name="failure-verdict-bypass-reachable",
    description=(
        "Detects when on_context_limit/on_rate_limit bypass routes from "
        "verdict-gated steps can reach success stop terminals"
    ),
    severity=Severity.ERROR,
)
def _check_failure_verdict_bypass_reachable(ctx: ValidationContext) -> list[RuleFinding]:
    findings: list[RuleFinding] = []
    # Reuse the context's pre-built routing graph rather than rebuilding it. Beyond
    # avoiding a redundant O(steps) build, ctx.step_graph reflects the effective
    # routing edges when the caller supplied them, which _build_step_graph(ctx.recipe)
    # would not — matching what every other ctx.step_graph-based rule analyzes.
    full_graph = ctx.step_graph

    for step_name, step in ctx.recipe.steps.items():
        if step.tool != "run_skill":
            continue

        if not _has_routed_failure_verdict(step, ctx):
            continue

        for bypass_kind, bypass_target in (
            ("on_context_limit", step.on_context_limit),
            ("on_rate_limit", step.on_rate_limit),
        ):
            if not bypass_target:
                continue
            reachable = _bfs_capped(full_graph, {bypass_target}, set())
            for reached_name in reachable:
                reached_step = ctx.recipe.steps.get(reached_name)
                if (
                    reached_step
                    and reached_step.action == "stop"
                    and is_success_stop(reached_step)
                ):
                    findings.append(
                        make_finding(
                            rule_name="failure-verdict-bypass-reachable",
                            step_name=step_name,
                            message=(
                                f"Step '{step_name}' has failure verdicts routed via on_result "
                                f"but {bypass_kind} routes to '{bypass_target}' which can reach "
                                f"success stop '{reached_name}', bypassing verdict routing"
                            ),
                        )
                    )
                    break

    return findings
