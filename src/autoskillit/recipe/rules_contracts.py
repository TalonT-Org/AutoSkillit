"""Semantic rules for skill contract completeness."""

from __future__ import annotations

from autoskillit.core.types import Severity
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
