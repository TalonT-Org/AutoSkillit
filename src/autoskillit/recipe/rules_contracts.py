"""Semantic rules for skill contract completeness."""

from __future__ import annotations

import re as _re

from autoskillit.core import Severity
from autoskillit.recipe._analysis import ValidationContext
from autoskillit.recipe.contracts import (
    get_skill_contract,
    load_bundled_manifest,
    resolve_skill_name,
)
from autoskillit.recipe.registry import RuleFinding, semantic_rule


@semantic_rule(
    name="missing-output-patterns",
    description=(
        "Flag run_skill steps whose skill has file_path outputs but empty expected_output_patterns"
    ),
    severity=Severity.WARNING,
)
def _check_missing_output_patterns(ctx: ValidationContext) -> list[RuleFinding]:
    """Flag run_skill steps with file_path outputs but no expected_output_patterns."""
    findings: list[RuleFinding] = []
    manifest = load_bundled_manifest()

    for step_name, step in ctx.recipe.steps.items():
        if step.tool != "run_skill":
            continue

        skill_cmd = step.with_args.get("skill_command", "")
        if "${{" in skill_cmd:
            continue

        name = resolve_skill_name(skill_cmd)
        if not name:
            continue

        contract = get_skill_contract(name, manifest)
        if not contract:
            continue

        file_outputs = [o for o in contract.outputs if o.type == "file_path"]
        if file_outputs and not contract.expected_output_patterns:
            findings.append(
                RuleFinding(
                    rule="missing-output-patterns",
                    severity=Severity.WARNING,
                    step_name=step_name,
                    message=(
                        f"Skill '{name}' has {len(file_outputs)} file_path output(s) "
                        f"but no expected_output_patterns. Session output validation "
                        f"is inactive for this skill."
                    ),
                )
            )

    return findings


@semantic_rule(
    name="pattern-examples-match",
    description=(
        "Flag run_skill steps whose expected_output_patterns do not match "
        "any declared pattern_examples string"
    ),
    severity=Severity.ERROR,
)
def _check_pattern_examples_match(ctx: ValidationContext) -> list[RuleFinding]:
    """For skills with both patterns and examples, all patterns must match at least one
    example. A mismatch is a definitive bug — the pattern will never match valid output."""
    findings: list[RuleFinding] = []
    manifest = load_bundled_manifest()

    for step_name, step in ctx.recipe.steps.items():
        if step.tool != "run_skill":
            continue
        skill_cmd = step.with_args.get("skill_command", "")
        if "${{" in skill_cmd:
            continue
        name = resolve_skill_name(skill_cmd)
        if not name:
            continue
        contract = get_skill_contract(name, manifest)
        if not contract or not contract.expected_output_patterns or not contract.pattern_examples:
            continue
        for pattern in contract.expected_output_patterns:
            if not any(_re.search(pattern, ex) for ex in contract.pattern_examples):
                findings.append(
                    RuleFinding(
                        rule="pattern-examples-match",
                        severity=Severity.ERROR,
                        step_name=step_name,
                        message=(
                            f"Skill '{name}': pattern {pattern!r} does not match "
                            f"any pattern_examples {contract.pattern_examples!r}. "
                            f"The pattern can never match valid skill output."
                        ),
                    )
                )
    return findings


@semantic_rule(
    name="missing-pattern-examples",
    description="Flag run_skill steps with expected_output_patterns but no pattern_examples",
    severity=Severity.WARNING,
)
def _check_missing_pattern_examples(ctx: ValidationContext) -> list[RuleFinding]:
    """If a skill has patterns, it must also declare pattern_examples."""
    findings: list[RuleFinding] = []
    manifest = load_bundled_manifest()

    for step_name, step in ctx.recipe.steps.items():
        if step.tool != "run_skill":
            continue
        skill_cmd = step.with_args.get("skill_command", "")
        if "${{" in skill_cmd:
            continue
        name = resolve_skill_name(skill_cmd)
        if not name:
            continue
        contract = get_skill_contract(name, manifest)
        if not contract:
            continue
        if contract.expected_output_patterns and not contract.pattern_examples:
            findings.append(
                RuleFinding(
                    rule="missing-pattern-examples",
                    severity=Severity.WARNING,
                    step_name=step_name,
                    message=(
                        f"Skill '{name}' has expected_output_patterns but no "
                        f"pattern_examples. Add pattern_examples to skill_contracts.yaml "
                        f"so patterns can be statically validated."
                    ),
                )
            )
    return findings
