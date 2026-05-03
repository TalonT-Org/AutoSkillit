"""Semantic rules for skill verdict routing completeness and consistency.

Ensures that recipe steps capturing a skill verdict with declared allowed_values
have an explicit on_result condition for every allowed value. A catch-all
`when: true` route does not count as explicit handling.

Also ensures that steps invoking the same skill route the same verdict value
to the same outcome category (continuation vs escalation).
"""

from __future__ import annotations

import re

from autoskillit.core import Severity, get_logger
from autoskillit.recipe._analysis import ValidationContext
from autoskillit.recipe.contracts import load_bundled_manifest
from autoskillit.recipe.registry import RuleFinding, semantic_rule

logger = get_logger(__name__)

_SKILL_NAME_RE = re.compile(r"/autoskillit:([\w-]+)")


def _get_allowed_values_for_skill(skill_name: str) -> dict[str, list[str]]:
    """Return {output_name: [allowed_value, ...]} for a skill's outputs with allowed_values."""
    try:
        manifest = load_bundled_manifest()
    except Exception:
        logger.warning(
            "unrouted-verdict-value: failed to load bundled manifest; skipping", exc_info=True
        )
        return {}
    skill_contract = manifest.get("skills", {}).get(skill_name, {})
    result: dict[str, list[str]] = {}
    for output in skill_contract.get("outputs", []):
        if "allowed_values" in output:
            result[output["name"]] = output["allowed_values"]
    return result


def _extract_skill_name(skill_command: str) -> str | None:
    """Extract skill name from a skill_command like /autoskillit:review-pr ..."""
    m = _SKILL_NAME_RE.match(skill_command.strip())
    return m.group(1) if m else None


def _is_explicit_condition(when: str | None, value: str) -> bool:
    """A condition is explicit if it references the value and is not the catch-all."""
    if not when:
        return False
    if when.strip() == "true":
        return False
    return bool(re.search(r"\b" + re.escape(value) + r"\b", when))


@semantic_rule(
    name="unrouted-verdict-value",
    description=(
        "Each allowed verdict value from a skill contract must have an explicit "
        "on_result condition. A catch-all 'when: true' does not count as explicit handling."
    ),
    severity=Severity.ERROR,
)
def _check_unrouted_verdict_values(ctx: ValidationContext) -> list[RuleFinding]:
    """Error when any on_result step lets an allowed verdict value fall to a catch-all.

    For each step that:
    - uses tool: run_skill
    - invokes a skill with declared allowed_values on a captured output
    - has an on_result block

    Verify that every allowed value has an explicit on_result condition
    (not just a when: "true" catch-all).
    """
    findings: list[RuleFinding] = []

    for step_name, step in ctx.recipe.steps.items():
        if step.tool != "run_skill":
            continue
        skill_command = (step.with_args or {}).get("skill_command", "")
        skill_name = _extract_skill_name(skill_command)
        if not skill_name:
            continue

        allowed_by_output = _get_allowed_values_for_skill(skill_name)
        if not allowed_by_output:
            continue

        capture = step.capture or {}
        for output_name, allowed_values in allowed_by_output.items():
            # Check that the output is captured
            captured_key = None
            for cap_key, cap_expr in capture.items():
                if not isinstance(cap_expr, str):
                    continue
                if f"result.{output_name}" in cap_expr:
                    captured_key = cap_key
                    break
            if not captured_key:
                continue  # Not captured — dataflow rules handle this separately

            if not step.on_result:
                continue  # No on_result — other rules handle missing routing

            conditions = step.on_result.conditions or []
            # Only check outputs that are actually used as routing discriminators.
            # Context-forwarding captures (e.g. experiment_type) have allowed_values
            # for schema validation, not for routing — skip them here.
            if not any(c.when and f"result.{output_name}" in c.when for c in conditions):
                continue

            unrouted = [
                value
                for value in allowed_values
                if not any(_is_explicit_condition(c.when, value) for c in conditions)
            ]
            # Allow at most one value to fall through the catch-all (the intended default).
            # Fire only when two or more values share the catch-all — that is the
            # silent-fallthrough bug pattern (e.g. needs_human treated identically to approved).
            if len(unrouted) <= 1:
                continue
            for value in unrouted:
                findings.append(
                    RuleFinding(
                        rule="unrouted-verdict-value",
                        severity=Severity.ERROR,
                        step_name=step_name,
                        message=(
                            f"Step '{step_name}' captures '{output_name}' from "
                            f"'{skill_name}' but has no explicit on_result condition "
                            f"for allowed value '{value}'. Add a condition like: "
                            f'when: "${{{{ result.{output_name} }}}} == {value}". '
                            f"A catch-all 'when: true' does not count."
                        ),
                    )
                )

    return findings


def _classify_route_target(target: str) -> str:
    """Classify a route target as 'escalation' or 'continuation'."""
    if "failure" in target or "escalat" in target or "stop" in target:
        return "escalation"
    return "continuation"


@semantic_rule(
    name="verdict-routing-asymmetry",
    description=(
        "Steps invoking the same skill must route the same verdict value to the "
        "same outcome category (continuation vs escalation). Asymmetric routing "
        "causes flaky tests to abort in one path while retrying in another."
    ),
    severity=Severity.ERROR,
)
def _check_verdict_routing_asymmetry(ctx: ValidationContext) -> list[RuleFinding]:
    """Error when the same verdict routes to different outcome categories across steps."""
    findings: list[RuleFinding] = []

    # Build map: {(skill_name, verdict_value): [(step_name, classification), ...]}
    routing_map: dict[tuple[str, str], list[tuple[str, str]]] = {}

    for step_name, step in ctx.recipe.steps.items():
        if step.tool != "run_skill":
            continue
        skill_command = (step.with_args or {}).get("skill_command", "")
        skill_name = _extract_skill_name(skill_command)
        if not skill_name:
            continue

        allowed_by_output = _get_allowed_values_for_skill(skill_name)
        if not allowed_by_output:
            continue

        if not step.on_result:
            continue

        conditions = step.on_result.conditions or []
        for _output_name, allowed_values in allowed_by_output.items():
            for value in allowed_values:
                for cond in conditions:
                    if _is_explicit_condition(cond.when, value):
                        classification = _classify_route_target(cond.route)
                        key = (skill_name, value)
                        routing_map.setdefault(key, []).append((step_name, classification))
                        break

    for (skill_name, value), entries in routing_map.items():
        classifications = {cls for _, cls in entries}
        if len(classifications) <= 1:
            continue
        escalation_steps = [name for name, cls in entries if cls == "escalation"]
        continuation_steps = [name for name, cls in entries if cls == "continuation"]
        if escalation_steps and continuation_steps:
            findings.append(
                RuleFinding(
                    rule="verdict-routing-asymmetry",
                    severity=Severity.ERROR,
                    step_name=escalation_steps[0],
                    message=(
                        f"Verdict '{value}' from '{skill_name}' routes to escalation "
                        f"in step '{escalation_steps[0]}' but continuation in step "
                        f"'{continuation_steps[0]}'. All steps invoking the same skill "
                        f"should treat the same verdict consistently."
                    ),
                )
            )

    return findings


# Regex to extract a literal verdict value from a `when` expression.
# Handles both template form: ${{ result.verdict }} == value
# and bare dot-access form:    result.verdict == 'value'
_VALUE_FROM_WHEN_RE = re.compile(
    r"result\.(?P<output>[\w]+)"  # result.<output_name>
    r"\s*\}?\}?"  # optional closing braces from template form
    r"\s*==\s*"  # equality operator
    r"(?P<value>'\w+'|\w+)"  # balanced single-quoted or bare value
)


@semantic_rule(
    name="on-result-values-in-allowed-values",
    description=(
        "Every literal verdict value referenced in a recipe on_result condition "
        "must be present in the skill contract's allowed_values. Catches drift "
        "between recipe routing and contract definitions."
    ),
    severity=Severity.ERROR,
)
def _check_on_result_values_in_allowed_values(ctx: ValidationContext) -> list[RuleFinding]:
    """Error when a recipe routes a verdict value not declared in allowed_values.

    Completes the closed-loop constraint: recipe routes → allowed_values (this rule)
    → examples (example-covers-all-allowed-values) → patterns (all-examples-match-all-patterns).
    """
    findings: list[RuleFinding] = []

    for step_name, step in ctx.recipe.steps.items():
        if step.tool != "run_skill":
            continue
        skill_command = (step.with_args or {}).get("skill_command", "")
        skill_name = _extract_skill_name(skill_command)
        if not skill_name:
            continue

        allowed_by_output = _get_allowed_values_for_skill(skill_name)
        if not allowed_by_output:
            continue

        if not step.on_result:
            continue

        conditions = step.on_result.conditions or []
        for condition in conditions:
            when = condition.when
            if not when or when.strip() == "true":
                continue
            for m in _VALUE_FROM_WHEN_RE.finditer(when):
                output_name = m.group("output")
                value = m.group("value").strip("'")
                if output_name not in allowed_by_output:
                    continue
                if value not in allowed_by_output[output_name]:
                    findings.append(
                        RuleFinding(
                            rule="on-result-values-in-allowed-values",
                            severity=Severity.ERROR,
                            step_name=step_name,
                            message=(
                                f"Step '{step_name}' routes {output_name} == '{value}' "
                                f"but skill '{skill_name}' contract allowed_values does "
                                f"not include '{value}'. Add '{value}' to allowed_values "
                                f"in skill_contracts.yaml."
                            ),
                        )
                    )

    return findings
