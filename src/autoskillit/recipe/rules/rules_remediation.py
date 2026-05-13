"""audit-impl remediation_path capture must have non-terminal non-GO route."""

from autoskillit.core import Severity, resolve_skill_name
from autoskillit.recipe._analysis import ValidationContext
from autoskillit.recipe.registry import RuleFinding, semantic_rule

TERMINAL_SENTINELS = frozenset(("done", "escalate"))


def _is_non_terminal_route(ctx: ValidationContext, target: str) -> bool:
    if target in TERMINAL_SENTINELS:
        return False
    step = ctx.recipe.steps.get(target)
    return step is not None and step.action != "stop"


def _has_non_terminal_non_go_route(ctx: ValidationContext, on_result) -> bool:
    if on_result is None:
        return False

    if on_result.conditions:
        for cond in on_result.conditions:
            when = cond.when
            if when is not None and "result.error" in when:
                continue
            if when is not None and "GO" in when and "NO GO" not in when:
                continue
            if _is_non_terminal_route(ctx, cond.route):
                return True
        return False
    else:
        for verdict, target in on_result.routes.items():
            if verdict == "GO":
                continue
            if _is_non_terminal_route(ctx, target):
                return True
        return False


@semantic_rule(
    name="audit-impl-remediation-route",
    description=(
        "A recipe step invoking audit-impl that captures remediation_path must "
        "have at least one on_result route for non-GO verdicts that targets a "
        "non-terminal step. Routing all non-GO outcomes to a terminal stop "
        "action discards the remediation file, preventing the closed-loop "
        "audit-remediate-replan cycle."
    ),
    severity=Severity.ERROR,
)
def _check_audit_impl_remediation_route(ctx: ValidationContext) -> list[RuleFinding]:
    findings = []
    for step_name, step in ctx.recipe.steps.items():
        if step.tool != "run_skill":
            continue
        skill = resolve_skill_name(step.with_args.get("skill_command", ""))
        if skill != "audit-impl":
            continue
        if not any("result.remediation_path" in v for v in step.capture.values()):
            continue
        if not _has_non_terminal_non_go_route(ctx, step.on_result):
            findings.append(
                RuleFinding(
                    rule="audit-impl-remediation-route",
                    severity=Severity.ERROR,
                    step_name=step_name,
                    message=(
                        f"Step '{step_name}' captures 'remediation_path' from audit-impl "
                        f"but all non-GO on_result routes target terminal steps. "
                        f"At least one non-GO route must target a non-terminal step "
                        f"(e.g., a 'remediate' routing step) so the remediation file "
                        f"can be consumed by a downstream re-plan step."
                    ),
                )
            )
    return findings
