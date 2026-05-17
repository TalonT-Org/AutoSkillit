"""Semantic rules for optional capture guard enforcement."""

from __future__ import annotations

import regex as re

from autoskillit.core import Severity
from autoskillit.recipe._analysis import ValidationContext
from autoskillit.recipe.contracts import (
    get_skill_contract,
    load_bundled_manifest,
    resolve_skill_name,
)
from autoskillit.recipe.registry import RuleFinding, semantic_rule
from autoskillit.recipe.schema import RecipeStep


def _has_optional_capture_group(patterns: list[str]) -> bool:
    """Return True if any pattern contains a fully-optional capture group.

    A fully-optional capture group is a (...) expression followed by ?
    at the end of the pattern, e.g. ``(https://...)?`` makes the entire URL optional.
    """
    for pattern in patterns:
        if re.search(r"\([^)]+\)\?$", pattern):
            return True
    return False


def _has_guard_for_key(
    step_name: str,
    captured_key: str,
    steps: dict[str, RecipeStep],
) -> bool:
    """Check whether a truthiness guard for ``captured_key`` is interposed before the consumer.

    Walks the route chain starting from ``step_name`` via ``on_success`` and ``on_result``.
    Returns True if:
    - An ``action: route`` step is found whose ``on_result`` contains a
      ``when: ${{ context.<captured_key> }}`` condition, OR
    - A non-route step's own ``on_result`` gates on ``result.<captured_key>``
      (self-guard: consumers are only reachable when the value is truthy).
    """
    visited: set[str] = set()
    to_visit: list[str] = [step_name]

    while to_visit:
        current = to_visit.pop(0)
        if current in visited:
            continue
        visited.add(current)
        step = steps.get(current)
        if step is None:
            continue

        # Check if this step is a guard: action=route with on_result conditions
        if step.action == "route" and step.on_result and step.on_result.conditions:
            for cond in step.on_result.conditions:
                if cond.when and f"context.{captured_key}" in cond.when:
                    return True
            # Route steps with conditions terminate the chain (they don't use on_success)
            continue

        if step.on_result and step.on_result.conditions:
            # Non-route step routing via on_result: self-guard if it gates on result.{key}
            # before routing to consumers (only reachable when value is truthy).
            if any(
                cond.when and f"result.{captured_key}" in cond.when
                for cond in step.on_result.conditions
            ):
                return True
            # Follow each condition's route to find downstream guards
            for cond in step.on_result.conditions:
                if cond.route:
                    to_visit.append(cond.route)
        elif step.on_success:
            to_visit.append(step.on_success)

    return False


@semantic_rule(
    name="optional-capture-requires-guard",
    description=(
        "Flag run_skill steps whose skill contract has an optional capture group "
        "(...)? in expected_output_patterns but route on_success to a consumer "
        "without a truthiness guard on the captured value."
    ),
    severity=Severity.WARNING,
)
def _check_optional_capture_requires_guard(ctx: ValidationContext) -> list[RuleFinding]:
    """Detect when a step with optional output patterns routes to a consumer without a guard."""
    findings: list[RuleFinding] = []
    manifest = load_bundled_manifest()

    for step_name, step in ctx.recipe.steps.items():
        if step.tool != "run_skill":
            continue

        skill_cmd = step.with_args.get("skill_command", "")
        name = resolve_skill_name(skill_cmd)
        if not name:
            continue

        contract = get_skill_contract(name, manifest)
        if not contract:
            continue

        # Check if the contract has an optional capture group
        if not _has_optional_capture_group(contract.expected_output_patterns):
            continue

        # The step must capture something (otherwise no value to guard)
        if not step.capture:
            continue

        # The step must route somewhere — either on_success or on_result
        routes_via_on_result = bool(step.on_result and step.on_result.conditions)
        if not step.on_success and not routes_via_on_result:
            continue

        route_target = step.on_success or "(via on_result)"

        # For each captured key, check whether a guard is interposed
        for captured_key in step.capture:
            if not _has_guard_for_key(step_name, captured_key, ctx.recipe.steps):
                findings.append(
                    RuleFinding(
                        rule="optional-capture-requires-guard",
                        severity=Severity.WARNING,
                        step_name=step_name,
                        message=(
                            f"Step '{step_name}' has an optional capture group in "
                            f"expected_output_patterns for skill '{name}' but routes "
                            f"to '{route_target}' without a truthiness guard "
                            f"on the captured value '{captured_key}'. Add an action:route "
                            f"step with 'when: ${{ context.{captured_key} }}' before "
                            f"routing to a consumer."
                        ),
                    )
                )

    return findings
