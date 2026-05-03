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

# Skill names covered by the result-field-drift rule.
_RESULT_FIELD_DRIFT_SKILLS = frozenset(
    {
        "planner-generate-phases",
        "planner-elaborate-phase",
    }
)

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
        r"(?:and|,)\s*exit\s+0",  # graceful early exit suffix (e.g. "and exit 0", ", exit 0")
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
                    findings.append(
                        RuleFinding(
                            rule="always-has-no-write-exit",
                            severity=Severity.WARNING,
                            step_name=step_name,
                            message=(
                                f"Skill '{name}' SKILL.md at {skill_md} could not be read; "
                                f"always-has-no-write-exit check was skipped for this step."
                            ),
                        )
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


@semantic_rule(
    name="result-field-drift",
    description=(
        "result_fields declared in skill_contracts.yaml must match the TypedDict required keys "
        "in planner/schema.py for planner skills that produce structured result files."
    ),
    severity=Severity.ERROR,
)
def _check_result_field_drift(ctx: ValidationContext) -> list[RuleFinding]:
    """Error when a skill's declared result_fields diverge from the canonical TypedDict keys.

    Covers planner-generate-phases, elaborate-phase, elaborate-assignment, and elaborate-wp.
    Comparison is one-directional: contract required fields vs TypedDict required keys.
    """
    # Deferred import: recipe/ must not import planner/ at module level (REQ-COMP-009).
    # planner/ is IL-1 and does not import recipe/, so no circular risk.
    from autoskillit.planner import (  # noqa: PLC0415
        PHASE_REQUIRED_KEYS,
    )

    skill_schemas: dict[str, frozenset[str]] = {
        "planner-generate-phases": PHASE_REQUIRED_KEYS,
        "planner-elaborate-phase": PHASE_REQUIRED_KEYS,
    }

    findings: list[RuleFinding] = []
    manifest = load_bundled_manifest()

    for step_name, step in ctx.recipe.steps.items():
        if step.tool != "run_skill":
            continue

        skill_cmd = step.with_args.get("skill_command", "")
        name = resolve_skill_name(skill_cmd)
        if not name or name not in skill_schemas:
            continue

        contract = get_skill_contract(name, manifest)
        if contract is None or not contract.result_fields:
            continue

        expected_keys = skill_schemas[name]
        declared_required = {rf.name for rf in contract.result_fields if rf.required}

        added = declared_required - expected_keys
        removed = expected_keys - declared_required

        if added or removed:
            parts: list[str] = []
            if added:
                parts.append(f"extra in contract: {sorted(added)}")
            if removed:
                parts.append(f"missing from contract: {sorted(removed)}")
            findings.append(
                RuleFinding(
                    rule="result-field-drift",
                    severity=Severity.ERROR,
                    step_name=step_name,
                    message=(
                        f"Skill '{name}' result_fields in skill_contracts.yaml diverge from "
                        f"the TypedDict required keys in planner/schema.py — {'; '.join(parts)}. "
                        f"Update result_fields to match the TypedDict definition."
                    ),
                )
            )

    return findings


@semantic_rule(
    name="example-covers-all-allowed-values",
    description=(
        "Every allowed_value declared on a skill output must appear in at least "
        "one pattern_examples entry, ensuring the example set covers all output paths"
    ),
    severity=Severity.ERROR,
)
def _check_example_covers_all_allowed_values(ctx: ValidationContext) -> list[RuleFinding]:
    """Error when any allowed_value has no corresponding pattern_examples entry.

    Reads allowed_values from the raw manifest dict (not SkillContract, which does not
    promote allowed_values into SkillOutput). An example 'covers' a value when the
    example text contains a match for '{output_name}\\s*=\\s*{re.escape(value)}'.
    """
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
        if not contract or not contract.pattern_examples:
            continue

        skill_dict = manifest.get("skills", {}).get(name, {})
        for output in skill_dict.get("outputs", []):
            if "allowed_values" not in output:
                continue
            output_name: str = output["name"]
            for value in output["allowed_values"]:
                pattern = _re.compile(_re.escape(output_name) + r"\s*=\s*" + _re.escape(value))
                if not any(pattern.search(ex) for ex in contract.pattern_examples):
                    findings.append(
                        RuleFinding(
                            rule="example-covers-all-allowed-values",
                            severity=Severity.ERROR,
                            step_name=step_name,
                            message=(
                                f"Skill '{name}': allowed_value '{value}' on output "
                                f"'{output_name}' has no pattern_examples entry. "
                                f"Add an example containing '{output_name} = {value}' "
                                f"to skill_contracts.yaml so all output paths are covered."
                            ),
                        )
                    )

    return findings


@semantic_rule(
    name="all-examples-match-all-patterns",
    description=(
        "Every pattern_examples entry must satisfy ALL expected_output_patterns. "
        "An example that fails a pattern indicates the pattern is conditional, "
        "which will cause CONTRACT_VIOLATION at runtime due to AND semantics."
    ),
    severity=Severity.ERROR,
)
def _check_all_examples_match_all_patterns(ctx: ValidationContext) -> list[RuleFinding]:
    """Error when any pattern_examples entry fails any expected_output_pattern.

    The existing pattern-examples-match rule checks ∀ pattern, ∃ example (pattern not dead).
    This rule checks ∀ example, ∀ pattern (pattern not conditional). Together they form a
    complete constraint: patterns are both necessary and sufficient for all output paths.
    """
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
        if not contract or not contract.pattern_examples or not contract.expected_output_patterns:
            continue

        for example in contract.pattern_examples:
            for pattern in contract.expected_output_patterns:
                try:
                    matched = bool(_re.search(pattern, example))
                except _re.error:
                    continue  # invalid regex — covered by pattern-examples-match
                if not matched:
                    preview = repr(example[:60])
                    findings.append(
                        RuleFinding(
                            rule="all-examples-match-all-patterns",
                            severity=Severity.ERROR,
                            step_name=step_name,
                            message=(
                                f"Skill '{name}': example {preview} does not match "
                                f"pattern {pattern!r}. This pattern is conditional — "
                                f"AND semantics in _check_expected_patterns will reject "
                                f"sessions producing this output."
                            ),
                        )
                    )

    return findings
