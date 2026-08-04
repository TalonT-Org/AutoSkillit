"""Semantic rule for note/with_args shape contradiction detection."""

from __future__ import annotations

import regex as re

from autoskillit.core import Severity
from autoskillit.recipe._analysis import ValidationContext
from autoskillit.recipe.registry import RuleFinding, make_finding, semantic_rule

# Patterns that indicate the note is instructing inline-arg concatenation.
# Phrasing variants found in the wild (4 recipes):
#   - "append it to the skill_command" (remediation, implementation-groups)
#   - "Appends number and complexity to skill_command:" (merge-prs)
#   - "Replace ${{ ... }} in the skill_command" (research)
#   - "/merge-pr {pr_number} {complexity}" (merge-prs — unquoted example)
_INLINE_APPEND_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    (
        "append-to-skill_command",
        re.compile(r"append\w*\s+.{0,40}?\bskill_command\b", re.IGNORECASE),
    ),
    (
        "embed-in-skill_command",
        re.compile(r"embed\s+(it\s+)?(in|into)\s+(the\s+)?skill_command", re.IGNORECASE),
    ),
    (
        "concatenate-to-skill_command",
        re.compile(r"concatenat\w*\s+.{0,40}?\bskill_command\b", re.IGNORECASE),
    ),
    (
        "replace-in-skill_command",
        re.compile(r"replace\s+.{0,60}?\b(in|within)\s+(the\s+)?skill_command\b", re.IGNORECASE),
    ),
    (
        "inline-arg-example-quoted",
        re.compile(r'"/autoskillit:\S+\s+[^"]*\{'),
    ),
    (
        "inline-arg-example-unquoted",
        re.compile(r"/autoskillit:\S+\s+\{"),
    ),
]


@semantic_rule(
    name="note-shape-contradiction",
    description=(
        "A run_skill step's note: field instructs inline-arg concatenation into "
        "skill_command while its with: block declares structured skill_inputs. "
        "The orchestrator will follow the note, construct the wrong shape, and "
        "be rejected by the runtime binder."
    ),
    severity=Severity.ERROR,
)
def _check_note_shape_contradiction(ctx: ValidationContext) -> list[RuleFinding]:
    findings: list[RuleFinding] = []
    for step_name, step in ctx.recipe.steps.items():
        note = step.note or ""
        if not note:
            continue

        # Only relevant for run_skill steps with compiled skill_inputs
        with_args = step.with_args or {}
        if "skill_inputs" not in with_args:
            continue

        # Skip steps where skill_command itself is a dynamic template
        # (e.g. "/autoskillit:arch-lens-{slug}"). These are multi-lens
        # fan-out steps where the note legitimately describes command-name
        # substitution, not stale inline-arg concatenation.
        skill_cmd = with_args.get("skill_command", "")
        if isinstance(skill_cmd, str) and "{" in skill_cmd:
            continue

        for phrase_name, pattern in _INLINE_APPEND_PATTERNS:
            if pattern.search(note):
                findings.append(
                    make_finding(
                        rule_name="note-shape-contradiction",
                        step_name=step_name,
                        message=(
                            f"Step '{step_name}' note instructs inline-arg concatenation "
                            f"('{phrase_name}') but with: block uses structured skill_inputs. "
                            f"Rewrite note to describe the skill_inputs shape. "
                            f"The stale instruction causes the orchestrator to build "
                            f"the wrong skill_command and triggers "
                            f"recipe_execution_static_tool_mismatch rejection."
                        ),
                    ),
                )
                break  # One finding per step is sufficient
    return findings
