"""Semantic rule: verdict-ungated-degradation.

Detects skill SKILL.md files where the graceful-degradation path emits the
same verdict as the nominal (work-performed) path.  A zero-validation run
becomes indistinguishable from a real review, allowing it to advance the
pipeline to CI/merge.
"""

from __future__ import annotations

import regex as re

from autoskillit.core import Severity, get_logger
from autoskillit.recipe._analysis import ValidationContext
from autoskillit.recipe._skill_helpers import (
    _resolve_skill_md,
    get_allowed_values_for_skill,
)
from autoskillit.recipe.contracts import resolve_skill_name
from autoskillit.recipe.registry import RuleFinding, semantic_rule

logger = get_logger(__name__)

# Patterns that identify a graceful-degradation trigger line in SKILL.md.
_DEGRADATION_TRIGGER_RE = re.compile(r"unavailable|graceful.{0,20}degrada", re.IGNORECASE)

# Matches a verdict emit instruction: `verdict=X` or `verdict = X` or `Output verdict=X`.
_VERDICT_EMIT_RE = re.compile(r"\bverdict\s*=\s*(\w+)")

_STEP_HEADING_RE = re.compile(r"^#{1,3}\s+Step\s+\d", re.IGNORECASE)

_NEGATION_RE = re.compile(r"\bnot\b|\bnever\b|\bmust not\b|\bdo not\b", re.IGNORECASE)

_SAFE_DEGRADATION_VERDICTS: frozenset[str] = frozenset({"needs_human", "changes_requested"})


def _find_degradation_verdict(content: str) -> str | None:
    """Return the verdict emitted on the graceful-degradation path, or None."""
    lines = content.splitlines()
    for i, line in enumerate(lines):
        if not _DEGRADATION_TRIGGER_RE.search(line):
            continue
        if _NEGATION_RE.search(line):
            continue
        end = min(len(lines), i + 15)
        for j in range(i + 1, end):
            if _STEP_HEADING_RE.match(lines[j]):
                end = j
                break
        window = "\n".join(lines[i + 1 : end])
        m = _VERDICT_EMIT_RE.search(window)
        if m:
            return m.group(1)
    return None


def _find_nominal_verdicts(content: str) -> set[str]:
    """Return verdict values emitted outside the degradation block.

    Excludes lines within 15 lines of any degradation trigger (that is not
    negated) so that the nominal path check is independent of what the
    degradation path emits.  Also stops the exclusion window at step headings.
    """
    lines = content.splitlines()
    excluded: set[int] = set()
    for i, line in enumerate(lines):
        if _DEGRADATION_TRIGGER_RE.search(line) and not _NEGATION_RE.search(line):
            end = min(len(lines), i + 15)
            for j in range(i + 1, end):
                if _STEP_HEADING_RE.match(lines[j]):
                    end = j
                    break
            for j in range(i, end):
                excluded.add(j)

    nominal_content = "\n".join(line for i, line in enumerate(lines) if i not in excluded)
    return set(_VERDICT_EMIT_RE.findall(nominal_content))


@semantic_rule(
    name="verdict-ungated-degradation",
    description=(
        "Verdict-emitting skills with graceful-degradation paths must not "
        "emit a verdict that is semantically indistinguishable from the "
        "nominal success verdict"
    ),
    severity=Severity.ERROR,
)
def _check_verdict_ungated_degradation(ctx: ValidationContext) -> list[RuleFinding]:
    """Scan recipe steps for skills whose SKILL.md shares a verdict across both paths."""
    findings: list[RuleFinding] = []

    for step_name, step in ctx.recipe.steps.items():
        if step.tool != "run_skill":
            continue

        skill_cmd = step.with_args.get("skill_command", "")
        name = resolve_skill_name(skill_cmd)
        if not name:
            continue

        # Only analyse skills that declare a verdict output with allowed_values.
        allowed_by_output = get_allowed_values_for_skill(name)
        verdict_allowed = allowed_by_output.get("verdict")
        if not verdict_allowed:
            continue

        # Locate and read the SKILL.md.
        skill_md_path = _resolve_skill_md(name)
        if skill_md_path is None:
            continue
        try:
            skill_md_content = skill_md_path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            logger.warning(
                "could not read %s; skipping verdict-ungated-degradation check",
                skill_md_path,
            )
            continue

        # Check that a degradation trigger phrase is present.
        if not _DEGRADATION_TRIGGER_RE.search(skill_md_content):
            continue

        degradation_verdict = _find_degradation_verdict(skill_md_content)
        if degradation_verdict is None:
            continue

        nominal_verdicts = _find_nominal_verdicts(skill_md_content)

        if degradation_verdict in _SAFE_DEGRADATION_VERDICTS:
            continue

        # The bug: the degradation verdict is ALSO used on the nominal path.
        if degradation_verdict in nominal_verdicts and degradation_verdict in verdict_allowed:
            findings.append(
                RuleFinding(
                    rule="verdict-ungated-degradation",
                    severity=Severity.ERROR,
                    step_name=step_name,
                    message=(
                        f"Skill '{name}' emits verdict='{degradation_verdict}' on its "
                        f"graceful-degradation path but '{degradation_verdict}' is also "
                        f"used by the nominal (work-performed) path.  A zero-validation "
                        f"run is indistinguishable from a real review.  Use a distinct "
                        f"verdict (e.g., 'needs_human') for degradation paths."
                    ),
                )
            )

    return findings
