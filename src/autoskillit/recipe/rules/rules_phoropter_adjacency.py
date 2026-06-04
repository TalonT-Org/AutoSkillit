"""Semantic validation rules enforcing phoropter step adjacency (dial→apply→synthesize).

Phase-order constraint: canonical phoropter phases ("dial", "apply", "synthesize")
must appear in the declared order within each family.  Steps with phoropter_family
set but a non-canonical step key (e.g., "select_review_dimensions") are transparent
to phase ordering — they are allowed between canonical phases without advancing the
phase counter.  Route-action steps are also transparent.
"""

from __future__ import annotations

from pathlib import Path

from autoskillit.core import Severity, load_yaml, pkg_root
from autoskillit.recipe._analysis import ValidationContext
from autoskillit.recipe._api_cache import YamlFileCache
from autoskillit.recipe.registry import RuleFinding, semantic_rule

_PHOROPTER_PHASES: tuple[str, ...] = ("dial", "apply", "synthesize")

_PREFIXES_CACHE = YamlFileCache()


def _load_registry_yaml(path: Path) -> dict[str, str | None]:
    try:
        data = load_yaml(path)
    except FileNotFoundError:
        return {}
    result: dict[str, str | None] = {}
    for family_name, entry in data.get("families", {}).items():
        result[family_name] = entry.get("step_naming", {}).get("prefix")
    return result


def _load_family_prefixes() -> dict[str, str | None]:
    path = pkg_root() / "assets" / "phoropter-registry.yaml"
    return _PREFIXES_CACHE.get_or_load(path, _load_registry_yaml)


def _canonical_phase_for_step(
    step_name: str,
    family: str | None,
    prefixes: dict[str, str | None],
) -> str | None:
    if step_name in _PHOROPTER_PHASES:
        return step_name
    if family is not None:
        prefix = prefixes.get(family)
        if prefix is not None:
            for phase in _PHOROPTER_PHASES:
                if step_name == f"{prefix}_{phase}":
                    return phase
    return None


@semantic_rule(
    name="phoropter-phase-order",
    description="Phoropter family steps must follow the dial→apply→synthesize phase progression.",
    severity=Severity.ERROR,
)
def _check_phoropter_phase_order(ctx: ValidationContext) -> list[RuleFinding]:
    if not any(step.phoropter_family for step in ctx.recipe.steps.values()):
        return []

    prefixes = _load_family_prefixes()
    findings: list[RuleFinding] = []
    expected_next: dict[str, str] = {}
    errored_families: set[str] = set()

    for step_name, step in ctx.recipe.steps.items():
        family = step.phoropter_family
        if family is None:
            continue
        if family in errored_families:
            continue
        if step.action == "route":
            continue
        canonical = _canonical_phase_for_step(step_name, family, prefixes)
        if canonical is None:
            continue

        expected_phase = expected_next.get(family, "dial")
        if canonical != expected_phase:
            findings.append(
                RuleFinding(
                    rule="phoropter-phase-order",
                    severity=Severity.ERROR,
                    step_name=step_name,
                    message=(
                        f"Step '{step_name}' (canonical: '{canonical}') in phoropter family "
                        f"'{family}' is out of order: expected phase '{expected_phase}', "
                        f"got '{canonical}'."
                    ),
                )
            )
            errored_families.add(family)
        else:
            phase_idx = _PHOROPTER_PHASES.index(canonical)
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

    prefixes = _load_family_prefixes()
    findings: list[RuleFinding] = []
    in_progress: dict[str, str] = {}
    next_expected: dict[str, int] = {}
    completed: set[str] = set()

    for step_name, step in ctx.recipe.steps.items():
        family = step.phoropter_family
        if family is not None:
            if step.action == "route":
                continue
            if family in completed:
                continue
            canonical = _canonical_phase_for_step(step_name, family, prefixes)
            if canonical is None:
                continue
            in_progress[family] = step_name
            idx = _PHOROPTER_PHASES.index(canonical)
            if idx == next_expected.get(family, 0):
                next_expected[family] = idx + 1
                if idx + 1 == len(_PHOROPTER_PHASES):
                    del in_progress[family]
                    del next_expected[family]
                    completed.add(family)
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
