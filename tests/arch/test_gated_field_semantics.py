"""Closed-world guard for outcome fields that influence adjudication."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from autoskillit.core import parse_outcome_expression
from autoskillit.recipe.contracts import load_bundled_manifest

pytestmark = [pytest.mark.layer("arch"), pytest.mark.small]

_ROOT = Path(__file__).resolve().parents[2]
_FAMILY_MEMBERS = {
    "thread-resolvers": (
        "resolve-review",
        "resolve-research-review",
        "resolve-claims-review",
    )
}
_SKILL_TO_FAMILY = {
    skill_name: family for family, members in _FAMILY_MEMBERS.items() for skill_name in members
}
_DERIVED_COUNTERS = {
    "accept_count",
    "fixes_applied",
    "fix_failures",
    "skipped_in_fix_phase",
}
_LEGACY_AGGREGATE_INSTRUCTIONS = (
    "Count any failed call toward `fix_failures`",
    "A failed call increments `fix_failures`",
    "{accept_count - skipped_in_fix_phase}",
    "Track: accept_count",
)


@dataclass(frozen=True, slots=True)
class GatedFieldDef:
    field_name: str
    skill_family: str
    normative_anchor: str


GATED_OUTPUT_FIELDS = (
    GatedFieldDef(
        "accept_count",
        "thread-resolvers",
        "Server derivation: `accept_count` equals the number of `finding_disposition` rows.",
    ),
    GatedFieldDef(
        "fixes_applied",
        "thread-resolvers",
        "Server derivation: `fixes_applied` equals the number of `applied` rows.",
    ),
    GatedFieldDef(
        "fix_failures",
        "thread-resolvers",
        "Server derivation: `fix_failures` equals the number of `failed` rows.",
    ),
)


def _skill_text(skill_name: str) -> str:
    path = _ROOT / "src" / "autoskillit" / "skills_extended" / skill_name / "SKILL.md"
    return path.read_text(encoding="utf-8")


def _delimited_block(content: str, name: str) -> str:
    pattern = re.compile(
        rf"<!-- {re.escape(name)}:begin -->\s*(?P<body>.*?)\s*"
        rf"<!-- {re.escape(name)}:end -->",
        re.DOTALL,
    )
    matches = list(pattern.finditer(content))
    assert len(matches) == 1, f"expected exactly one {name} block"
    return matches[0]["body"]


def _manifest_gated_fields(manifest: dict[str, Any]) -> set[tuple[str, str]]:
    referenced: set[tuple[str, str]] = set()
    for skill_name, contract in manifest["skills"].items():
        family = _SKILL_TO_FAMILY.get(skill_name, f"unregistered:{skill_name}")
        expressions = [
            expression
            for invariant in contract.get("outcome_invariants", [])
            for expression in (invariant["when"], invariant["require"])
        ]
        expressions.extend(
            qualifier["when"] for qualifier in contract.get("success_qualifiers", [])
        )
        for expression in expressions:
            comparisons = parse_outcome_expression(expression)
            assert comparisons is not None, f"loader accepted invalid expression: {expression}"
            referenced.update((family, item.field_name) for item in comparisons)
    return referenced


def _semantics_violations(content: str, definitions: tuple[GatedFieldDef, ...]) -> list[str]:
    violations: list[str] = []
    for definition in definitions:
        count = content.count(definition.normative_anchor)
        if count == 0:
            violations.append(f"missing normative anchor: {definition.normative_anchor}")
        elif count > 1:
            violations.append(f"duplicate normative anchor: {definition.normative_anchor}")
    for legacy in _LEGACY_AGGREGATE_INSTRUCTIONS:
        if legacy in content:
            violations.append(f"legacy aggregate instruction present: {legacy}")
    return violations


def test_gated_field_inventory_is_complete() -> None:
    manifest = load_bundled_manifest()
    expected = {
        (definition.skill_family, definition.field_name) for definition in GATED_OUTPUT_FIELDS
    }

    assert _manifest_gated_fields(manifest) == expected


@pytest.mark.parametrize("family, members", _FAMILY_MEMBERS.items())
def test_every_gated_field_has_a_single_normative_definition(
    family: str,
    members: tuple[str, ...],
) -> None:
    definitions = tuple(
        definition for definition in GATED_OUTPUT_FIELDS if definition.skill_family == family
    )
    assert definitions

    for skill_name in members:
        body = _delimited_block(_skill_text(skill_name), "gated-field-semantics")
        assert _semantics_violations(body, definitions) == []


def test_every_declared_qualifier_is_documented() -> None:
    manifest = load_bundled_manifest()
    for skill_name, contract in manifest["skills"].items():
        for qualifier in contract.get("success_qualifiers", []):
            assert qualifier["qualifier"] in _skill_text(skill_name), (
                f"{skill_name} does not document qualifier {qualifier['qualifier']}"
            )


@pytest.mark.parametrize("skill_name", _FAMILY_MEMBERS["thread-resolvers"])
def test_resolver_templates_emit_only_model_owned_fields(skill_name: str) -> None:
    body = _delimited_block(_skill_text(skill_name), "gated-field-semantics")
    templates = "\n".join(
        (
            _delimited_block(body, "resolver-operative-output"),
            _delimited_block(body, "resolver-final-output"),
        )
    )

    assert "review_status = processed" in templates
    assert "finding_disposition = {finding-id} | {applied|skipped|failed}" in templates
    for counter in _DERIVED_COUNTERS:
        assert not re.search(rf"^{counter}\s*=", templates, re.MULTILINE)


def test_semantics_detector_canaries_pin_exact_violations() -> None:
    definition = GATED_OUTPUT_FIELDS[0]
    duplicate = f"{definition.normative_anchor}\n{definition.normative_anchor}"
    assert _semantics_violations(duplicate, (definition,)) == [
        f"duplicate normative anchor: {definition.normative_anchor}"
    ]

    legacy = _LEGACY_AGGREGATE_INSTRUCTIONS[0]
    assert _semantics_violations(legacy, (definition,)) == [
        f"missing normative anchor: {definition.normative_anchor}",
        f"legacy aggregate instruction present: {legacy}",
    ]
