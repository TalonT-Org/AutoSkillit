"""Tests for recipe contract dataclasses.

Verifies that SkillOutput carries the optional allowed_values field that the
callable verdict routing rules depend on (recipe-routing-deadlock immunity, #3889).
"""

from __future__ import annotations

import pytest

from autoskillit.recipe._contracts_types import SkillContract, SkillInput, SkillOutput

pytestmark = [pytest.mark.layer("recipe"), pytest.mark.small]


def test_skill_input_validates_explicit_absence_value() -> None:
    optional = SkillInput(name="note", type="string", required=False, absence_value="")
    assert optional.has_absence_value
    assert optional.absence_value == ""

    with pytest.raises(ValueError, match="required input"):
        SkillInput(name="note", type="string", required=True, absence_value="")
    with pytest.raises(ValueError, match="does not accept"):
        SkillInput(name="count", type="integer", required=False, absence_value="")


def test_literal_absent_is_not_structural_absence() -> None:
    item = SkillInput(name="note", type="string", required=False, absence_value="absent")
    assert item.has_absence_value
    structural = SkillInput(name="note", type="string", required=False)
    assert not structural.has_absence_value
    assert structural.absence_value is None


@pytest.mark.parametrize("input_type", ["number", "float"])
def test_skill_input_rejects_noncanonical_float(input_type: str) -> None:
    """Runtime-bound values must remain encodable by the canonical hash profile."""
    skill_input = SkillInput(name="value", type=input_type, required=True)

    assert skill_input.accepts(1)
    assert not skill_input.accepts(1.5)


@pytest.mark.parametrize("absence_value", [1.5, {}, []])
def test_skill_input_rejects_non_scalar_absence_value(
    absence_value: object,
) -> None:
    with pytest.raises(ValueError, match="absence_value must be a strict scalar"):
        SkillInput(
            name="value",
            type="string",
            required=False,
            absence_value=absence_value,  # type: ignore[arg-type]
        )


def test_skill_contract_rejects_unknown_input_preflight() -> None:
    with pytest.raises(ValueError, match="unsupported input preflight"):
        SkillContract(inputs=(), outputs=[], input_preflight="unknown")


def test_skill_contract_accepts_supported_input_preflight() -> None:
    contract = SkillContract(inputs=(), outputs=[], input_preflight="audit_cycle_inventory")

    assert contract.input_preflight == "audit_cycle_inventory"


def test_skill_output_accepts_allowed_values_kwarg() -> None:
    """SkillOutput must accept an `allowed_values` keyword argument and store it."""
    output = SkillOutput(
        name="committed",
        type="str",
        allowed_values=["false", "true", "regression_detected"],
    )
    assert output.name == "committed"
    assert output.type == "str"
    assert output.allowed_values == ["false", "true", "regression_detected"]


def test_skill_output_allowed_values_defaults_to_empty_list() -> None:
    """SkillOutput must default `allowed_values` to an empty list (backward compat)."""
    output = SkillOutput(name="verdict", type="str")
    assert output.allowed_values == []


def test_skill_output_preserves_order_of_allowed_values() -> None:
    """SkillOutput must preserve the order of allowed_values as supplied."""
    expected = ["z", "a", "m", "b"]
    output = SkillOutput(name="x", type="str", allowed_values=expected)
    assert output.allowed_values == expected
