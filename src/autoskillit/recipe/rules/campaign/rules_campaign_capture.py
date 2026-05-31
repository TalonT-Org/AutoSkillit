"""Campaign dispatch capture and sentinel validation rules."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

import regex as re

from autoskillit.core import Severity, get_logger, pkg_root
from autoskillit.recipe._analysis import ValidationContext
from autoskillit.recipe._contracts_types import RESULT_CAPTURE_RE
from autoskillit.recipe._rule_helpers import (
    _extract_sentinel_fields,
    _identify_optional_output_fields,
    _is_failure_sentinel_value,
    _load_dispatch_target,
    extract_sentinel_json_blocks,
)
from autoskillit.recipe.contracts import (
    get_skill_contract,
    load_bundled_manifest,
    resolve_skill_name,
)
from autoskillit.recipe.io import find_recipe_by_name, load_recipe
from autoskillit.recipe.registry import RuleFinding, semantic_rule
from autoskillit.recipe.schema import RecipeKind

if TYPE_CHECKING:
    from autoskillit.recipe.schema import Recipe

logger = get_logger(__name__)

_IDENT_RE = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_]*$")
_RESULT_TEMPLATE_RE = re.compile(r"^\$\{\{\s*result\.[\w-]+\s*\}\}$")
_RESULT_FIELD_RE = re.compile(r"^\$\{\{\s*result\.([\w-]+)\s*\}\}$")


@semantic_rule(
    name="dispatch-capture-keys-are-identifiers",
    description="Capture keys must be valid Python identifiers",
    severity=Severity.ERROR,
)
def _check_dispatch_capture_keys_are_identifiers(ctx: ValidationContext) -> list[RuleFinding]:
    if ctx.recipe.kind != RecipeKind.CAMPAIGN:
        return []
    findings = []
    for d in ctx.recipe.dispatches:
        for key in d.capture:
            if not _IDENT_RE.match(key):
                findings.append(
                    RuleFinding(
                        rule="dispatch-capture-keys-are-identifiers",
                        severity=Severity.ERROR,
                        step_name="(top-level)",
                        message=(
                            f"Dispatch {d.name!r} capture key {key!r} is not a valid"
                            " identifier. Use only letters, digits, and underscores"
                            " (must start with letter or _)."
                        ),
                    )
                )
    return findings


@semantic_rule(
    name="dispatch-capture-value-references-result",
    description="Capture values must use ${{ result.field }} syntax",
    severity=Severity.ERROR,
)
def _check_dispatch_capture_value_references_result(ctx: ValidationContext) -> list[RuleFinding]:
    if ctx.recipe.kind != RecipeKind.CAMPAIGN:
        return []
    findings = []
    for d in ctx.recipe.dispatches:
        for key, entry in d.capture.items():
            if not _RESULT_TEMPLATE_RE.match(entry.from_.strip()):
                findings.append(
                    RuleFinding(
                        rule="dispatch-capture-value-references-result",
                        severity=Severity.ERROR,
                        step_name="(top-level)",
                        message=(
                            f"Dispatch {d.name!r} capture[{key!r}] value {entry.from_!r} must use "
                            "${{ result.<field_name> }} syntax."
                        ),
                    )
                )
    return findings


def _extract_sentinel_fields_per_stop(recipe: Recipe) -> list[frozenset[str]]:
    """Extract field sets from each individual sentinel stop step.

    Returns a list of frozensets, one per success sentinel stop, rather than
    the union. This enables per-path validation. Failure-terminal sentinels
    (those whose example JSON contains ``"success": false``) are excluded
    because failure paths do not produce captured result fields.
    """
    per_stop: list[frozenset[str]] = []
    for step in recipe.steps.values():
        if step.action != "stop" or not step.message:
            continue
        if "sentinel" not in step.message.lower():
            continue
        fields: set[str] = set()
        is_failure_sentinel = False
        for block in extract_sentinel_json_blocks(step.message):
            try:
                parsed = json.loads(block)
                if isinstance(parsed, dict):
                    _sv = parsed.get("success")
                    if _is_failure_sentinel_value(_sv):
                        is_failure_sentinel = True
                        break
                    fields.update(parsed.keys())
            except (json.JSONDecodeError, ValueError):
                logger.debug("sentinel_json_parse_failed", step=step.name, raw=block)
                continue
        if not is_failure_sentinel and fields:
            per_stop.append(frozenset(fields))
    return per_stop


@semantic_rule(
    name="dispatch-capture-field-in-sentinel",
    description="Captured result fields should appear in target recipe's sentinel message",
    severity=Severity.ERROR,
)
def _check_dispatch_capture_field_in_sentinel(ctx: ValidationContext) -> list[RuleFinding]:
    if ctx.recipe.kind != RecipeKind.CAMPAIGN:
        return []
    findings: list[RuleFinding] = []
    for d in ctx.recipe.dispatches:
        if d.gate or not d.capture:
            continue
        target = _load_dispatch_target(d, ctx.project_dir)
        if target is None:
            continue
        sentinel_fields = _extract_sentinel_fields(target)
        if not sentinel_fields:
            findings.append(
                RuleFinding(
                    rule="dispatch-capture-field-in-sentinel",
                    severity=Severity.ERROR,
                    step_name="(top-level)",
                    message=(
                        f"Dispatch {d.name!r} has captures but target recipe "
                        f"{d.recipe!r} has no parseable sentinel stop step. Add an "
                        f"'Example sentinel: {{...}}' JSON block to the success stop step."
                    ),
                )
            )
            continue
        for cap_key, cap_val in d.capture.items():
            match = _RESULT_FIELD_RE.match(cap_val.from_.strip())
            if not match:
                continue
            field_name = match.group(1)
            if field_name not in sentinel_fields:
                findings.append(
                    RuleFinding(
                        rule="dispatch-capture-field-in-sentinel",
                        severity=Severity.ERROR,
                        step_name="(top-level)",
                        message=(
                            f"Dispatch {d.name!r} captures {cap_key!r} as "
                            f"${{{{ result.{field_name} }}}} but target recipe "
                            f"{d.recipe!r} sentinel does not list field {field_name!r}. "
                            f"Known: {sorted(sentinel_fields)}."
                        ),
                    )
                )
    return findings


@semantic_rule(
    name="dispatch-capture-field-in-all-sentinels",
    description="Captured result fields must appear in ALL sentinel stop paths of target recipe",
    severity=Severity.ERROR,
)
def _check_dispatch_capture_field_in_all_sentinels(
    ctx: ValidationContext,
) -> list[RuleFinding]:
    if ctx.recipe.kind != RecipeKind.CAMPAIGN:
        return []
    findings: list[RuleFinding] = []
    for d in ctx.recipe.dispatches:
        if d.gate or not d.capture:
            continue
        target = _load_dispatch_target(d, ctx.project_dir)
        if target is None:
            continue
        per_stop = _extract_sentinel_fields_per_stop(target)
        if len(per_stop) < 2:
            continue
        for cap_key, cap_val in d.capture.items():
            match = _RESULT_FIELD_RE.match(cap_val.from_.strip())
            if not match:
                continue
            field_name = match.group(1)
            missing_in = [i for i, fields in enumerate(per_stop) if field_name not in fields]
            if missing_in:
                findings.append(
                    RuleFinding(
                        rule="dispatch-capture-field-in-all-sentinels",
                        severity=Severity.ERROR,
                        step_name="(top-level)",
                        message=(
                            f"Dispatch {d.name!r} captures {cap_key!r} as "
                            f"${{{{ result.{field_name} }}}} but not all sentinel stop "
                            f"paths in {d.recipe!r} emit field {field_name!r}. Missing "
                            f"from {len(missing_in)} of {len(per_stop)} paths."
                        ),
                    )
                )
    return findings


@semantic_rule(
    name="dispatch-capture-type-matches-contract-optionality",
    description=(
        "Flag campaign dispatches whose capture value_type='string' but the target "
        "recipe's skill contract allows an empty value for that field."
    ),
    severity=Severity.ERROR,
)
def _check_dispatch_capture_type_matches_contract_optionality(
    ctx: ValidationContext,
) -> list[RuleFinding]:
    """Detect string captures on fields that target-recipe skill contracts allow to be empty."""
    if ctx.recipe.kind != RecipeKind.CAMPAIGN:
        return []
    findings: list[RuleFinding] = []
    manifest = load_bundled_manifest()

    for d in ctx.recipe.dispatches:
        if not d.capture:
            continue

        target = _load_dispatch_target(d, ctx.project_dir)
        if target is None:
            info = find_recipe_by_name(d.recipe, pkg_root())
            if info is not None:
                try:
                    target = load_recipe(info.path)
                except Exception:
                    logger.debug(
                        "dispatch_target_load_failed_fallback", recipe=d.recipe, exc_info=True
                    )
            if target is None:
                findings.append(
                    RuleFinding(
                        rule="dispatch-capture-type-matches-contract-optionality",
                        severity=Severity.WARNING,
                        step_name="(top-level)",
                        message=(
                            f"Cannot verify capture type compatibility for dispatch "
                            f"{d.name!r} — target recipe {d.recipe!r} could not be loaded."
                        ),
                    )
                )
                continue

        # Collect all skill contracts from run_skill steps in the target recipe
        contracts = []
        for step in target.steps.values():
            if step.tool != "run_skill":
                continue
            skill_cmd = step.with_args.get("skill_command", "")
            name = resolve_skill_name(skill_cmd)
            if not name:
                continue
            contract = get_skill_contract(name, manifest)
            if contract is not None:
                contracts.append(contract)

        if not contracts:
            continue

        for cap_key, cap_entry in d.capture.items():
            m = RESULT_CAPTURE_RE.match(cap_entry.from_.strip())
            if not m:
                continue
            field_name = m.group(1)
            for contract in contracts:
                optional_fields = _identify_optional_output_fields(contract)
                if field_name in optional_fields and cap_entry.value_type == "string":
                    findings.append(
                        RuleFinding(
                            rule="dispatch-capture-type-matches-contract-optionality",
                            severity=Severity.ERROR,
                            step_name="(top-level)",
                            message=(
                                f"Dispatch {d.name!r} capture key {cap_key!r} captures "
                                f"field '{field_name}' with value_type='string' but target "
                                f"recipe {d.recipe!r} skill contract allows empty values "
                                f"for this field. Use 'type: optional_string'."
                            ),
                        )
                    )
                    break  # one finding per cap_key is enough

    return findings
