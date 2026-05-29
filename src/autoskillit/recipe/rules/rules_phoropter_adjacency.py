"""Semantic validation rules enforcing phoropter step adjacency (dial→apply→synthesize)."""

from __future__ import annotations

from autoskillit.core import Severity
from autoskillit.recipe._analysis import ValidationContext
from autoskillit.recipe.registry import RuleFinding, semantic_rule

_PHOROPTER_PHASES: tuple[str, ...] = ("dial", "apply", "synthesize")


@semantic_rule(
    name="phoropter-phase-order",
    description="Phoropter family steps must follow the dial→apply→synthesize phase progression.",
    severity=Severity.ERROR,
)
def _check_phoropter_phase_order(ctx: ValidationContext) -> list[RuleFinding]:
    if not any(step.phoropter_family for step in ctx.recipe.steps.values()):
        return []

    findings: list[RuleFinding] = []
    expected_next: dict[str, str] = {}
    errored_families: set[str] = set()

    for step_name, step in ctx.recipe.steps.items():
        family = step.phoropter_family
        if family is None:
            continue
        if family in errored_families:
            continue

        expected_phase = expected_next.get(family, "dial")
        if step_name != expected_phase:
            findings.append(
                RuleFinding(
                    rule="phoropter-phase-order",
                    severity=Severity.ERROR,
                    step_name=step_name,
                    message=(
                        f"Step '{step_name}' in phoropter family '{family}' is out of order: "
                        f"expected phase '{expected_phase}', got '{step_name}'."
                    ),
                )
            )
            errored_families.add(family)
        else:
            if step_name in _PHOROPTER_PHASES:
                phase_idx = _PHOROPTER_PHASES.index(step_name)
                if phase_idx + 1 < len(_PHOROPTER_PHASES):
                    expected_next[family] = _PHOROPTER_PHASES[phase_idx + 1]
                else:
                    expected_next.pop(family, None)

    return findings


@semantic_rule(
    name="phoropter-step-interleaving",
    description="Non-phoropter steps must not interrupt an in-progress phoropter family sequence.",
    severity=Severity.ERROR,
)
def _check_phoropter_interleaving(ctx: ValidationContext) -> list[RuleFinding]:
    if not any(step.phoropter_family for step in ctx.recipe.steps.values()):
        return []

    findings: list[RuleFinding] = []
    in_progress: dict[str, str] = {}
    next_expected: dict[str, int] = {}

    for step_name, step in ctx.recipe.steps.items():
        family = step.phoropter_family
        if family is not None:
            in_progress[family] = step_name
            if step_name in _PHOROPTER_PHASES:
                idx = _PHOROPTER_PHASES.index(step_name)
                if idx == next_expected.get(family, 0):
                    next_expected[family] = idx + 1
                    if idx + 1 == len(_PHOROPTER_PHASES):
                        del in_progress[family]
                        del next_expected[family]
        else:
            for fam, last_phase in list(in_progress.items()):
                findings.append(
                    RuleFinding(
                        rule="phoropter-step-interleaving",
                        severity=Severity.ERROR,
                        step_name=step_name,
                        message=(
                            f"Step '{step_name}' interrupts phoropter family '{fam}' "
                            f"which is in progress (last phase: '{last_phase}'). "
                            f"Move this step before or after the family's complete sequence."
                        ),
                    )
                )

    return findings
