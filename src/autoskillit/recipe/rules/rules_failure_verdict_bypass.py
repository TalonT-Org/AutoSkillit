"""Detects when bypass routes from verdict-gated steps can reach success stop terminals.

on_context_limit and on_rate_limit bypass on_result routing entirely. If a step
declares failure verdicts via on_result but its bypass route can reach a success
stop terminal, the failure verdict is silently circumvented.
"""

from __future__ import annotations

from autoskillit.core import Severity, resolve_skill_name
from autoskillit.recipe._analysis import ValidationContext
from autoskillit.recipe._analysis_bfs import _bfs_capped, _build_step_graph
from autoskillit.recipe._rule_helpers import is_success_stop
from autoskillit.recipe._skill_helpers import get_allowed_values_for_skill
from autoskillit.recipe.registry import RuleFinding, semantic_rule


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
    full_graph = _build_step_graph(ctx.recipe)

    for step_name, step in ctx.recipe.steps.items():
        if step.tool != "run_skill":
            continue

        if not step.on_result:
            continue

        skill_command = str((step.with_args or {}).get("skill_command") or "")
        skill_name = resolve_skill_name(skill_command)
        if not skill_name:
            continue

        allowed_by_output = get_allowed_values_for_skill(skill_name)
        if not allowed_by_output:
            continue

        conditions = step.on_result.conditions or []
        has_failure_verdict = False
        for _output_name, allowed_values in allowed_by_output.items():
            for value in allowed_values:
                for cond in conditions:
                    if cond.when and value in cond.when and cond.route:
                        target_step = ctx.recipe.steps.get(cond.route)
                        if target_step and not is_success_stop(target_step):
                            has_failure_verdict = True
                            break
                if has_failure_verdict:
                    break
            if has_failure_verdict:
                break

        if not has_failure_verdict:
            continue

        bypass_targets: list[tuple[str, str]] = []
        if step.on_context_limit:
            bypass_targets.append(("on_context_limit", step.on_context_limit))
        if step.on_rate_limit:
            bypass_targets.append(("on_rate_limit", step.on_rate_limit))

        for bypass_kind, bypass_target in bypass_targets:
            reachable = _bfs_capped(full_graph, {bypass_target}, set())
            for reached_name in reachable:
                reached_step = ctx.recipe.steps.get(reached_name)
                if (
                    reached_step
                    and reached_step.action == "stop"
                    and is_success_stop(reached_step)
                ):
                    findings.append(
                        RuleFinding(
                            rule="failure-verdict-bypass-reachable",
                            severity=Severity.ERROR,
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
