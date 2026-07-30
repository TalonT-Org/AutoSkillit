"""Require exhaustive server-authored audit outcome routing in protocol recipes."""

from __future__ import annotations

import regex as re

from autoskillit.core import Severity
from autoskillit.recipe._analysis import ValidationContext
from autoskillit.recipe.contracts import resolve_skill_name
from autoskillit.recipe.registry import RuleFinding, make_finding, semantic_rule
from autoskillit.recipe.schema import RecipeStep, StepResultCondition

_AUDIT_PROTOCOL_RECIPES = frozenset(
    {
        "implementation",
        "implementation-groups",
        "merge-prs",
        "remediation",
        "research",
        "research-implement",
    }
)
_INFRASTRUCTURE_STATUSES = (
    "CONFLICT",
    "STORAGE_FAILURE",
    "QUARANTINED",
    "NON_PUBLISHED_STANDALONE",
)
_PUBLISHED_STATUSES = ("PUBLISHED", "EXACT_REPLAY")
_STATUS_RE = re.compile(r"\bresult\.audit_status\s*==\s*([A-Z_]+)")
_VERDICT_RE = re.compile(r"\bresult\.audit_verdict\s*==\s*(NO GO|GO)\b")


def _audit_step(recipe_step: RecipeStep) -> bool:
    if recipe_step.tool != "run_skill":
        return False
    return resolve_skill_name(str(recipe_step.with_args.get("skill_command", ""))) == "audit-impl"


def _normalized_condition(condition: StepResultCondition) -> str:
    return (condition.when or "").replace("${{", "").replace("}}", "").strip()


def _condition_key(condition: StepResultCondition) -> tuple[str | None, str | None]:
    normalized = _normalized_condition(condition)
    status_match = _STATUS_RE.search(normalized)
    verdict_match = _VERDICT_RE.search(normalized)
    return (
        status_match.group(1) if status_match else None,
        verdict_match.group(1) if verdict_match else None,
    )


def _capture_source(step: RecipeStep, name: str) -> str | None:
    capture = step.capture.get(name)
    return capture.from_ if capture is not None else None


def _routing_violations(step: RecipeStep) -> list[str]:
    violations: list[str] = []
    if step.on_result is None or not step.on_result.conditions:
        return ["on_result must exhaustively route server-authored audit outcomes"]

    conditions = step.on_result.conditions
    keyed = {
        _condition_key(condition): (index, condition.route)
        for index, condition in enumerate(conditions)
    }
    required_keys = {
        ("SEMANTIC_REJECTED", None),
        *((status, None) for status in _INFRASTRUCTURE_STATUSES),
        *((status, verdict) for status in _PUBLISHED_STATUSES for verdict in ("GO", "NO GO")),
    }
    missing = sorted(required_keys - keyed.keys())
    if missing:
        violations.append(f"missing status/verdict routes: {missing}")

    if any("result.verdict" in _normalized_condition(condition) for condition in conditions):
        violations.append("child-authored result.verdict must not control audit routing")

    if not missing:
        first_verdict_index = min(keyed[(status, "GO")][0] for status in _PUBLISHED_STATUSES)
        pre_verdict_keys = {
            ("SEMANTIC_REJECTED", None),
            *((status, None) for status in _INFRASTRUCTURE_STATUSES),
        }
        if any(keyed[key][0] >= first_verdict_index for key in pre_verdict_keys):
            violations.append("semantic rejection and infrastructure statuses must route first")

        go_routes = {keyed[(status, "GO")][1] for status in _PUBLISHED_STATUSES}
        correction_routes = {
            keyed[("SEMANTIC_REJECTED", None)][1],
            *(keyed[(status, "NO GO")][1] for status in _PUBLISHED_STATUSES),
        }
        infrastructure_routes = {keyed[(status, None)][1] for status in _INFRASTRUCTURE_STATUSES}
        if len(go_routes) != 1:
            violations.append("PUBLISHED and EXACT_REPLAY GO must share one route")
        if len(correction_routes) != 1:
            violations.append(
                "semantic rejection and published/replayed NO GO must share one route"
            )
        if len(infrastructure_routes) != 1:
            violations.append("conflict, storage, quarantine, and standalone must share one route")

        error_routes = {
            condition.route
            for condition in conditions
            if _normalized_condition(condition) == "result.error"
        }
        default_routes = {condition.route for condition in conditions if condition.when is None}
        if len(error_routes) != 1 or error_routes != infrastructure_routes:
            violations.append("generic result.error must route to infrastructure failure")
        if len(default_routes) != 1 or default_routes != infrastructure_routes:
            violations.append("the catch-all route must be the infrastructure failure route")
        if go_routes & correction_routes or go_routes & infrastructure_routes:
            violations.append(
                "semantic success must not share correction or infrastructure routes"
            )
        if correction_routes & infrastructure_routes:
            violations.append(
                "semantic correction must not share the infrastructure failure route"
            )

    return violations


def _contract_violations(step: RecipeStep) -> list[str]:
    violations: list[str] = []
    required_captures = {
        "audit_status": "${{ result.audit_status }}",
        "audit_verdict": "${{ result.audit_verdict }}",
        "audit_attempt_id": "${{ result.audit_attempt_id }}",
    }
    for name, source in required_captures.items():
        if _capture_source(step, name) != source:
            violations.append(f"capture {name} from {source}")

    retry_source = step.with_args.get("retry_after_audit_attempt_id")
    if retry_source != "${{ context.audit_attempt_id }}":
        violations.append(
            "pass retry_after_audit_attempt_id as top-level context.audit_attempt_id"
        )
    skill_inputs = step.with_args.get("skill_inputs", {})
    if isinstance(skill_inputs, dict) and "retry_after_audit_attempt_id" in skill_inputs:
        violations.append("retry_after_audit_attempt_id must not appear inside skill_inputs")
    if "audit_attempt_id" not in step.optional_context_refs:
        violations.append("declare audit_attempt_id as an optional context reference")
    return violations


@semantic_rule(
    name="audit-outcome-routing-incomplete",
    description=(
        "Audit protocol recipes must capture the server-owned attempt and exhaustively route "
        "published/replayed verdicts, semantic correction, and infrastructure outcomes."
    ),
    severity=Severity.ERROR,
)
def _check_audit_outcome_routing(ctx: ValidationContext) -> list[RuleFinding]:
    if ctx.recipe.name not in _AUDIT_PROTOCOL_RECIPES:
        return []
    findings: list[RuleFinding] = []
    for step_name, step in ctx.recipe.steps.items():
        if not _audit_step(step):
            continue
        violations = [*_contract_violations(step), *_routing_violations(step)]
        if violations:
            findings.append(
                make_finding(
                    rule_name="audit-outcome-routing-incomplete",
                    step_name=step_name,
                    message=(
                        f"Step '{step_name}' has incomplete audit routing: "
                        f"{'; '.join(violations)}."
                    ),
                )
            )
    return findings
