"""Tests for recipe contract dataclasses.

Verifies that SkillOutput carries the optional allowed_values field that the
callable verdict routing rules depend on (recipe-routing-deadlock immunity, #3889).
"""

from __future__ import annotations

import pytest

from autoskillit.core import BoundValue, BoundValueOrigin, BoundValueState
from autoskillit.recipe._contracts_types import SkillContract, SkillInput, SkillOutput

pytestmark = [pytest.mark.layer("recipe"), pytest.mark.small]


def _bound_value(
    name: str,
    origin: BoundValueOrigin,
    *,
    dependency: str | None = None,
    declared_value: object = None,
    absence_value: str | int | bool | None = None,
) -> BoundValue:
    if origin is BoundValueOrigin.CONTEXT:
        assert dependency is not None
        declared_value = "${{ context." + dependency + " }}"
        context_dependencies = (dependency,)
        input_dependencies = ()
        template_dependencies = ()
    elif origin is BoundValueOrigin.RECIPE_INPUT:
        assert dependency is not None
        declared_value = "${{ inputs." + dependency + " }}"
        context_dependencies = ()
        input_dependencies = (dependency,)
        template_dependencies = ()
    elif origin is BoundValueOrigin.TEMPLATE:
        assert dependency is not None
        if declared_value is None:
            declared_value = "n-${{ context." + dependency + " }}"
        context_dependencies = (dependency,)
        input_dependencies = ()
        template_dependencies = ()
    else:
        context_dependencies = ()
        input_dependencies = ()
        template_dependencies = ()

    return BoundValue(
        name=name,
        declared_value=declared_value,
        effective_value=declared_value,
        state=BoundValueState.PRESENT,
        origin=origin,
        context_dependencies=context_dependencies,
        input_dependencies=input_dependencies,
        template_dependencies=template_dependencies,
        absence_value=absence_value,
    )


_WIRE_CASES = [
    ("integer", BoundValueOrigin.CONTEXT, "n", None, None, 31, 31),
    ("integer", BoundValueOrigin.CONTEXT, "n", None, None, "31", 31),
    ("integer", BoundValueOrigin.CONTEXT, "n", None, None, "0", 0),
    ("integer", BoundValueOrigin.CONTEXT, "n", None, None, "-5", -5),
    ("integer", BoundValueOrigin.CONTEXT, "n", None, None, "9" * 19, int("9" * 19)),
    ("integer", BoundValueOrigin.CONTEXT, "n", None, None, "031", None),
    ("integer", BoundValueOrigin.CONTEXT, "n", None, None, "+31", None),
    ("integer", BoundValueOrigin.CONTEXT, "n", None, None, " 31", None),
    ("integer", BoundValueOrigin.CONTEXT, "n", None, None, "31 ", None),
    ("integer", BoundValueOrigin.CONTEXT, "n", None, None, "31\n", None),
    ("integer", BoundValueOrigin.CONTEXT, "n", None, None, "3_1", None),
    ("integer", BoundValueOrigin.CONTEXT, "n", None, None, "31.0", None),
    ("integer", BoundValueOrigin.CONTEXT, "n", None, None, "", None),
    ("integer", BoundValueOrigin.CONTEXT, "n", None, None, "-0", None),
    ("integer", BoundValueOrigin.CONTEXT, "n", None, None, "٣١", None),
    ("integer", BoundValueOrigin.CONTEXT, "n", None, None, '"31"', None),
    ("integer", BoundValueOrigin.CONTEXT, "n", None, None, "9" * 20, None),
    ("integer", BoundValueOrigin.CONTEXT, "n", None, None, True, None),
    ("integer", BoundValueOrigin.CONTEXT, "n", None, None, False, None),
    ("integer", BoundValueOrigin.LITERAL, None, 31, None, 31, 31),
    ("integer", BoundValueOrigin.LITERAL, None, 31, None, "31", None),
    ("integer", BoundValueOrigin.TEMPLATE, "n", "n-${{ context.n }}", None, 31, 31),
    ("integer", BoundValueOrigin.TEMPLATE, "n", "n-${{ context.n }}", None, "31", None),
    ("boolean", BoundValueOrigin.CONTEXT, "enabled", None, None, True, True),
    ("boolean", BoundValueOrigin.CONTEXT, "enabled", None, None, False, False),
    ("boolean", BoundValueOrigin.CONTEXT, "enabled", None, None, "true", None),
    ("boolean", BoundValueOrigin.CONTEXT, "enabled", None, None, "false", None),
    ("boolean", BoundValueOrigin.CONTEXT, "enabled", None, None, 1, None),
    ("boolean", BoundValueOrigin.CONTEXT, "enabled", None, None, 0, None),
    ("string", BoundValueOrigin.CONTEXT, "label", None, None, "x", "x"),
    ("string", BoundValueOrigin.CONTEXT, "label", None, None, "", ""),
    ("string", BoundValueOrigin.CONTEXT, "label", None, None, 7, "7"),
    ("string", BoundValueOrigin.CONTEXT, "label", None, None, -3, "-3"),
    ("string", BoundValueOrigin.CONTEXT, "label", None, None, True, "true"),
    ("string", BoundValueOrigin.CONTEXT, "label", None, None, False, "false"),
    ("string", BoundValueOrigin.CONTEXT, "label", None, None, 0, "0"),
    ("string", BoundValueOrigin.RECIPE_INPUT, "label", None, None, False, "false"),
    ("string", BoundValueOrigin.CONTEXT, "label", None, "", 1, "1"),
    ("string", BoundValueOrigin.CONTEXT, "label", None, "", True, "true"),
    ("string", BoundValueOrigin.CONTEXT, "label", None, "", "", ""),
    ("string", BoundValueOrigin.CONTEXT, "label", None, "", False, "false"),
    ("string", BoundValueOrigin.CONTEXT, "label", None, "", 0, "0"),
    (
        "string",
        BoundValueOrigin.TEMPLATE,
        "n",
        "review-pr:${{ context.n }}",
        None,
        "review-pr:3",
        "review-pr:3",
    ),
    ("string", BoundValueOrigin.TEMPLATE, "n", "review-pr:${{ context.n }}", None, 3, None),
    ("string", BoundValueOrigin.TEMPLATE, "n", "review-pr:${{ context.n }}", None, True, None),
    ("string", BoundValueOrigin.LITERAL, None, "x", None, "x", "x"),
    ("string", BoundValueOrigin.LITERAL, None, "x", None, 7, None),
    ("file_path", BoundValueOrigin.CONTEXT, "path", None, None, "/a/b", "/a/b"),
    ("file_path", BoundValueOrigin.CONTEXT, "path", None, None, 1, None),
    ("file_path", BoundValueOrigin.CONTEXT, "path", None, None, True, None),
]


@pytest.mark.parametrize(
    (
        "input_type",
        "origin",
        "dependency",
        "declared_value",
        "absence_value",
        "wire",
        "expected",
    ),
    _WIRE_CASES,
)
def test_skill_input_admits_only_declared_type_wire_values(
    input_type: str,
    origin: BoundValueOrigin,
    dependency: str | None,
    declared_value: object,
    absence_value: str | None,
    wire: object,
    expected: object,
) -> None:
    skill_input = SkillInput(
        name="value",
        type=input_type,
        required=absence_value is None,
        absence_value=absence_value,
    )
    bound = _bound_value(
        "value",
        origin,
        dependency=dependency,
        declared_value=declared_value,
        absence_value=absence_value,
    )

    admitted = skill_input.admit_runtime_wire(wire, bound)
    if expected is None:
        assert admitted is None
    else:
        assert admitted == expected


@pytest.mark.parametrize(
    "wire",
    [pytest.param(10**19, id="19-digits"), pytest.param(10**5000, id="5000-digits")],
)
def test_skill_input_string_rejects_oversized_integer_without_raising(wire: int) -> None:
    skill_input = SkillInput(name="label", type="string", required=True)
    bound = _bound_value("label", BoundValueOrigin.CONTEXT, dependency="label")

    assert skill_input.admit_runtime_wire(wire, bound) is None


@pytest.mark.parametrize(
    ("wire", "bound_absence_value", "expected"),
    [
        (False, None, "false"),
        (0, None, "0"),
        (False, "", "false"),
        (0, "", "0"),
        (False, "false", None),
        (0, "0", None),
    ],
)
def test_skill_input_string_absence_policy_uses_bound_value_advertisement(
    wire: object,
    bound_absence_value: str | None,
    expected: str | None,
) -> None:
    skill_input = SkillInput("label", "string", False, absence_value="")
    bound = _bound_value(
        "label",
        BoundValueOrigin.CONTEXT,
        dependency="label",
        absence_value=bound_absence_value,
    )

    admitted = skill_input.admit_runtime_wire(wire, bound)
    if expected is None:
        assert admitted is None
    else:
        assert admitted == expected


def test_skill_input_wire_mismatch_messages_explain_expected_wire_shape() -> None:
    exact_integer = _bound_value("n", BoundValueOrigin.CONTEXT, dependency="n")
    assert SkillInput("n", "integer", True).wire_mismatch_message("31x", exact_integer) == (
        "runtime skill input 'n' expects 'integer'; received str; send a JSON integer or "
        "canonical ASCII decimal text (e.g. '31'; at most 19 digits)"
    )

    static_integer = _bound_value("n", BoundValueOrigin.LITERAL, declared_value=31)
    assert (
        SkillInput("n", "integer", True)
        .wire_mismatch_message("31", static_integer)
        .endswith("; send a JSON integer")
    )

    text_bound = _bound_value("label", BoundValueOrigin.CONTEXT, dependency="label")
    assert (
        SkillInput("label", "string", True)
        .wire_mismatch_message(10**19, text_bound)
        .endswith("; send a JSON string")
    )

    optional_text = SkillInput("label", "string", False, absence_value="")
    advertised_absence = _bound_value(
        "label",
        BoundValueOrigin.CONTEXT,
        dependency="label",
        absence_value="",
    )
    assert "absence" not in optional_text.wire_mismatch_message(False, advertised_absence)


def test_skill_input_accepts_stays_strict_for_declared_types() -> None:
    assert SkillInput("n", "integer", True).accepts("31") is False
    assert SkillInput("b", "boolean", True).accepts("false") is False


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
        SkillContract(inputs=(), outputs=[], input_preflight=("unknown",))


def test_skill_contract_accepts_supported_input_preflight() -> None:
    contract = SkillContract(inputs=(), outputs=[], input_preflight=("audit_cycle_inventory",))

    assert contract.input_preflight == ("audit_cycle_inventory",)


def test_skill_contract_rejects_scalar_input_preflight() -> None:
    with pytest.raises(ValueError, match="input_preflight must be a tuple"):
        SkillContract(inputs=(), outputs=[], input_preflight="audit_cycle_inventory")


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
