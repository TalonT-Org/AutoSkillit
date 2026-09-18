"""Contract-loader validation for outcome rules and structured outputs."""

from __future__ import annotations

import pytest

from autoskillit.recipe import get_skill_contract

pytestmark = [pytest.mark.layer("recipe"), pytest.mark.small]


def _manifest(skill_data: dict[str, object]) -> dict[str, object]:
    return {"skills": {"fake-skill": skill_data}}


def _integer_outputs() -> list[dict[str, str]]:
    return [
        {"name": "accept_count", "type": "integer"},
        {"name": "fix_failures", "type": "integer"},
        {"name": "retry_count", "type": "integer"},
    ]


def test_compound_outcome_rules_validate_every_conjunct() -> None:
    contract = get_skill_contract(
        "fake-skill",
        _manifest(
            {
                "outputs": _integer_outputs(),
                "outcome_invariants": [
                    {
                        "when": "accept_count > 0 and retry_count <= 1",
                        "require": "fix_failures == 0 and retry_count >= 0",
                    }
                ],
                "success_qualifiers": [
                    {
                        "when": "accept_count > 0 and fix_failures == 0",
                        "qualifier": "accepted_without_changes",
                    }
                ],
            }
        ),
    )

    assert contract is not None
    assert contract.outcome_invariants[0].require == "fix_failures == 0 and retry_count >= 0"
    assert contract.success_qualifiers[0].when == "accept_count > 0 and fix_failures == 0"


@pytest.mark.parametrize(
    ("invariant", "message"),
    [
        (
            {
                "when": "accept_count > 0 and undeclared_field == 0",
                "require": "fix_failures == 0",
            },
            "undeclared output",
        ),
        (
            {
                "when": "accept_count > 0",
                "require": "fix_failures == 0 and retry_count = 0",
            },
            "valid outcome expression",
        ),
        (
            {
                "when": "accept_count > 0 and verdict == 0",
                "require": "verdict == 0",
            },
            "non-integer output",
        ),
    ],
)
def test_outcome_invariant_expression_references_must_be_valid(
    invariant: dict[str, str], message: str
) -> None:
    manifest = _manifest(
        {
            "outputs": [*_integer_outputs(), {"name": "verdict", "type": "string"}],
            "outcome_invariants": [invariant],
        }
    )

    with pytest.raises(ValueError, match=message):
        get_skill_contract("fake-skill", manifest)


@pytest.mark.parametrize(
    ("when", "message"),
    [
        ("accept_count > 0 and undeclared_field == 0", "undeclared output"),
        ("accept_count => 0", "valid outcome expression"),
        ("verdict == 0", "non-integer output"),
    ],
)
def test_success_qualifier_expression_references_must_be_valid(when: str, message: str) -> None:
    manifest = _manifest(
        {
            "outputs": [*_integer_outputs(), {"name": "verdict", "type": "string"}],
            "success_qualifiers": [{"when": when, "qualifier": "accepted_without_changes"}],
        }
    )

    with pytest.raises(ValueError, match=message):
        get_skill_contract("fake-skill", manifest)


def test_outcome_invariant_missing_required_expression_fails_load() -> None:
    manifest = _manifest(
        {
            "outputs": _integer_outputs(),
            "outcome_invariants": [{"when": "accept_count > 0"}],
        }
    )

    with pytest.raises(ValueError, match="missing 'when' or 'require'"):
        get_skill_contract("fake-skill", manifest)


def test_dispositions_output_is_reserved_for_finding_disposition() -> None:
    contract = get_skill_contract(
        "fake-skill",
        _manifest({"outputs": [{"name": "finding_disposition", "type": "dispositions"}]}),
    )

    assert contract is not None
    assert contract.outputs[0].type == "dispositions"

    invalid_manifest = _manifest({"outputs": [{"name": "other_field", "type": "dispositions"}]})
    with pytest.raises(ValueError, match="reserved for 'finding_disposition'"):
        get_skill_contract("fake-skill", invalid_manifest)
