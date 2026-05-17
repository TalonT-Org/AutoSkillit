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
    Returns True if an ``action: route`` step is found whose ``on_result`` contains a
    ``when: ${{ context.<captured_key> }}`` condition.
    """
    visited: set[str] = set()
    current: str | None = step_name

    while current:
        if current in visited:
            break
        visited.add(current)
        step = steps.get(current)
        if step is None:
            break

        # Check if this step is a guard: action=route with on_result conditions
        if step.action == "route" and step.on_result and step.on_result.conditions:
            for cond in step.on_result.conditions:
                if cond.when and f"context.{captured_key}" in cond.when:
                    return True

        # Follow on_success for terminal routing, but NOT for action=route
        # (route steps handle routing via on_result, not on_success)
        if step.action == "route":
            # For route steps, the on_result determines routing; we still follow
            # on_success as a fallback only when there are no conditions
            if step.on_result and step.on_result.conditions:
                current = None
                continue
            else:
                current = step.on_success
        else:
            current = step.on_success

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

        # The step must route somewhere on success
        if not step.on_success:
            continue

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
                            f"on_success to '{step.on_success}' without a truthiness guard "
                            f"on the captured value '{captured_key}'. Add an action:route "
                            f"step with 'when: ${{ context.{captured_key} }}' before "
                            f"routing to a consumer."
                        ),
                    )
                )

    return findings


# Re-export for import convenience in tests
from autoskillit.recipe.schema import RecipeStep  # noqa: E402
