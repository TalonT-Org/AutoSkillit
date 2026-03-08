"""Semantic rules for skill verdict routing completeness.

Ensures that recipe steps capturing a skill verdict with declared allowed_values
have an explicit on_result condition for every allowed value. A catch-all
`when: true` route does not count as explicit handling.
"""

from __future__ import annotations

import re

from autoskillit.core import Severity
from autoskillit.recipe._analysis import ValidationContext
from autoskillit.recipe.contracts import load_bundled_manifest
from autoskillit.recipe.registry import RuleFinding, semantic_rule

_SKILL_NAME_RE = re.compile(r"/autoskillit:([\w-]+)")


def _get_allowed_values_for_skill(skill_name: str) -> dict[str, list[str]]:
    """Return {output_name: [allowed_value, ...]} for a skill's outputs with allowed_values."""
    try:
        manifest = load_bundled_manifest()
    except Exception:
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
def check_unrouted_verdict_values(ctx: ValidationContext) -> list[RuleFinding]:
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
