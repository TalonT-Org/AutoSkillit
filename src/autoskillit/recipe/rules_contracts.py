"""Semantic rules for skill contract completeness."""

from __future__ import annotations

import re as _re

from autoskillit.core import Severity, get_logger, pkg_root
from autoskillit.recipe._analysis import ValidationContext
from autoskillit.recipe.contracts import (
    get_skill_contract,
    load_bundled_manifest,
    resolve_skill_name,
)
from autoskillit.recipe.registry import RuleFinding, semantic_rule

logger = get_logger(__name__)


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
            try:
                matched = any(_re.search(pattern, ex) for ex in contract.pattern_examples)
            except _re.error:
                findings.append(
                    RuleFinding(
                        rule="pattern-examples-match",
                        severity=Severity.ERROR,
                        step_name=step_name,
                        message=(
                            f"Skill '{name}': pattern {pattern!r} is not a valid regex "
                            f"and cannot be matched against pattern_examples."
                        ),
                    )
                )
                continue
            if not matched:
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


_VALID_WRITE_BEHAVIORS = {"always", "conditional"}


@semantic_rule(
    name="write-behavior-consistency",
    description="Validates write_behavior declarations in skill contracts",
    severity=Severity.ERROR,
)
def _check_write_behavior_consistency(ctx: ValidationContext) -> list[RuleFinding]:
    """Validate write_behavior and write_expected_when contract declarations."""
    findings: list[RuleFinding] = []
    manifest = load_bundled_manifest()

    for step_name, step in ctx.recipe.steps.items():
        if step.tool != "run_skill":
            continue

        skill_cmd = step.with_args.get("skill_command", "")
        skill_name = resolve_skill_name(skill_cmd)
        if not skill_name:
            continue

        contract = get_skill_contract(skill_name, manifest)
        if contract is None:
            continue

        wb = contract.write_behavior
        wew = contract.write_expected_when

        if wb is not None and wb not in _VALID_WRITE_BEHAVIORS:
            findings.append(
                RuleFinding(
                    rule="write-behavior-consistency",
                    step_name=step_name,
                    message=(
                        f"Invalid write_behavior '{wb}'. "
                        "Must be 'always', 'conditional', or absent."
                    ),
                    severity=Severity.ERROR,
                )
            )
        if wb == "conditional" and not wew:
            findings.append(
                RuleFinding(
                    rule="write-behavior-consistency",
                    step_name=step_name,
                    message="write_behavior='conditional' requires non-empty write_expected_when.",
                    severity=Severity.ERROR,
                )
            )
        if wb == "always" and wew:
            findings.append(
                RuleFinding(
                    rule="write-behavior-consistency",
                    step_name=step_name,
                    message=(
                        "write_behavior='always' must not have "
                        "write_expected_when (contradictory)."
                    ),
                    severity=Severity.WARNING,
                )
            )
        for pattern in wew:
            try:
                _re.compile(pattern)
            except _re.error as exc:
                findings.append(
                    RuleFinding(
                        rule="write-behavior-consistency",
                        step_name=step_name,
                        message=f"Invalid regex in write_expected_when: {exc}",
                        severity=Severity.ERROR,
                    )
                )

    return findings


# ---------------------------------------------------------------------------
# always-has-no-write-exit: detect annotation mismatches
# ---------------------------------------------------------------------------

# Phrases in SKILL.md text that indicate a documented no-write success exit.
# Any skill with write_behavior='always' whose SKILL.md contains one of these
# phrases likely has a mismatched annotation and should use 'conditional' instead.
_ALWAYS_WITH_NO_WRITE_EXIT_PHRASES: frozenset[str] = frozenset(
    {
        "may be 0",  # resolve-failures: "may be 0 if tests were already passing"
        "nothing to do",
        "no changes needed",
        "already green",
        "graceful degradation",  # resolve-review: "graceful degradation — do not fail"
        r"\bskip\b.{0,30}\bstep\b",  # conditional path language
        "exit 0",  # graceful early exit
        r"\bif no\b.{0,30}\bfound\b",
        r"if.*unavailable",
        "already complete",
        "all phases",
    }
)


@semantic_rule(
    name="always-has-no-write-exit",
    description=(
        "Flag write_behavior='always' on skills whose SKILL.md documents a "
        "legitimate no-write success path"
    ),
    severity=Severity.ERROR,
)
def _check_always_has_no_write_exit(ctx: ValidationContext) -> list[RuleFinding]:
    """Detect contract annotation mismatches: 'always' on skills with documented no-write exits.

    Reads each 'always' skill's SKILL.md and searches for phrases indicating
    the skill can legitimately succeed with zero writes. If found, the skill
    must use 'conditional' write_behavior instead.
    """
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
        if contract is None or contract.write_behavior != "always":
            continue

        # Locate the skill's SKILL.md
        for skills_dir in ("skills", "skills_extended"):
            skill_md = pkg_root() / skills_dir / name / "SKILL.md"
            if skill_md.exists():
                try:
                    content = skill_md.read_text(encoding="utf-8").lower()
                except (OSError, UnicodeDecodeError):
                    logger.warning(
                        "could not read %s; skipping always-has-no-write-exit check",
                        skill_md,
                    )
                    break
                for phrase in _ALWAYS_WITH_NO_WRITE_EXIT_PHRASES:
                    if _re.search(phrase, content):
                        findings.append(
                            RuleFinding(
                                rule="always-has-no-write-exit",
                                severity=Severity.ERROR,
                                step_name=step_name,
                                message=(
                                    f"Skill '{name}' declares write_behavior='always' "
                                    f"but its SKILL.md contains phrase matching '{phrase}', "
                                    f"suggesting a legitimate no-write success path. "
                                    f"Change to write_behavior='conditional' with a "
                                    f"write_expected_when pattern tied to a structured "
                                    f"completion token."
                                ),
                            )
                        )
                        break  # one finding per step is enough
                break

    return findings
