"""Semantic rules for skip-inviting note text on optional recipe steps."""

from __future__ import annotations

from typing import TYPE_CHECKING

import regex as re

from autoskillit.core import Severity
from autoskillit.recipe._analysis import ValidationContext
from autoskillit.recipe.registry import RuleFinding, semantic_rule

if TYPE_CHECKING:
    pass


@semantic_rule(
    name="skip-inviting-note-text",
    description=(
        "A step with optional: true + skip_when_false has a note: field containing "
        "skip-inviting phrases ('never blocks', 'best-effort', 'optional: true'). "
        "These phrases recreate the skip-inviting signal even after the YAML field is stripped."
    ),
    severity=Severity.WARNING,
)
def _check_skip_inviting_notes(ctx: ValidationContext) -> list[RuleFinding]:
    """Flag note: fields that grant implicit skip permission on optional steps.

    Steps with optional: true + skip_when_false are mandatory when their guard
    resolves truthy. Note text using "never blocks", "best-effort", or "optional"
    recreates the skip-inviting signal even after the YAML field is stripped.
    """
    findings: list[RuleFinding] = []
    for step_name, step in ctx.recipe.steps.items():
        if not step.optional or not step.skip_when_false:
            continue
        note = step.note or ""
        if not note:
            continue
        if re.search(r"never\s+blocks?", note, re.IGNORECASE):
            findings.append(
                RuleFinding(
                    rule="skip-inviting-note-text",
                    severity=Severity.WARNING,
                    step_name=step_name,
                    message=(
                        f"Step '{step_name}' note contains skip-inviting phrase 'never blocks'. "
                        "Rewrite to imperative language: 'Execute this step. "
                        "Failures are logged but do not alter pipeline routing.'"
                    ),
                )
            )
        elif re.search(r"best[- ]effort", note, re.IGNORECASE):
            findings.append(
                RuleFinding(
                    rule="skip-inviting-note-text",
                    severity=Severity.WARNING,
                    step_name=step_name,
                    message=(
                        f"Step '{step_name}' note contains skip-inviting phrase 'best-effort'. "
                        "Rewrite to imperative language."
                    ),
                )
            )
        elif re.search(r"optional[=: ]+true", note, re.IGNORECASE):
            findings.append(
                RuleFinding(
                    rule="skip-inviting-note-text",
                    severity=Severity.WARNING,
                    step_name=step_name,
                    message=(
                        f"Step '{step_name}' note contains literal "
                        "'optional: true' or 'optional=true'. "
                        "Remove this prose metadata — the field is stripped server-side."
                    ),
                )
            )
    return findings
