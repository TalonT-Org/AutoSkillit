"""Canonical recipe invocation compiler coverage."""

from __future__ import annotations

import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

import autoskillit.recipe._binding as binding_module
from autoskillit.core import (
    ABSENT_BOUND_VALUE,
    BindingFailureCode,
    BindingMode,
    BoundStepInvocation,
    BoundValue,
    BoundValueOrigin,
    BoundValueState,
)
from autoskillit.recipe._binding import bind_recipe, bind_step_invocation
from autoskillit.recipe._contracts_manifest import get_skill_contract
from autoskillit.recipe._recipe_composition import _prune_skipped_steps
from autoskillit.recipe.io import load_recipe
from autoskillit.recipe.schema import RecipeStep

pytestmark = [pytest.mark.layer("recipe"), pytest.mark.small]


def _manifest() -> dict[str, object]:
    return {
        "skills": {
            "dry-walkthrough": {
                "inputs": [
                    {"name": "plan_path", "type": "file_path", "required": True},
                    {"name": "issue_url", "type": "string", "required": True},
                    {"name": "optional_note", "type": "string"},
                    {"name": "audit_cycle_path", "type": "file_path", "required": True},
                    {
                        "name": "plan_disposition_path",
                        "type": "file_path",
                        "required": True,
                    },
                    {"name": "enabled", "type": "boolean"},
                    {"name": "round", "type": "integer"},
                ]
            }
        }
    }


def _step(
    skill_inputs: dict[str, str | int | float | bool],
    **extra: str,
) -> RecipeStep:
    return RecipeStep(
        name="verify",
        tool="run_skill",
        with_args={
            "skill_command": "/autoskillit:dry-walkthrough",
            "cwd": "/repo",
            "skill_inputs": skill_inputs,
            **extra,
        },
    )


def _required_inputs() -> dict[str, str]:
    return {
        "plan_path": "/tmp/plans/current plan.md",
        "issue_url": "https://example.test/issues/42?x=a&y=b",
        "audit_cycle_path": "/tmp/audit/cycle.json",
        "plan_disposition_path": "/tmp/audit/plan disposition.json",
    }


@pytest.mark.parametrize(
    "declared,effective,state,origin",
    [
        ("declared", "effective", BoundValueState.ABSENT, BoundValueOrigin.ABSENT),
        (
            ABSENT_BOUND_VALUE,
            ABSENT_BOUND_VALUE,
            BoundValueState.PRESENT,
            BoundValueOrigin.LITERAL,
        ),
        (
            ABSENT_BOUND_VALUE,
            ABSENT_BOUND_VALUE,
            BoundValueState.ABSENT,
            BoundValueOrigin.LITERAL,
        ),
        ("declared", "effective", BoundValueState.PRESENT, BoundValueOrigin.ABSENT),
    ],
)
def test_bound_value_rejects_contradictory_absence(
    declared: object,
    effective: object,
    state: BoundValueState,
    origin: BoundValueOrigin,
) -> None:
    with pytest.raises(ValueError, match="absent"):
        BoundValue(
            name="value",
            declared_value=declared,
            effective_value=effective,
            state=state,
            origin=origin,
        )


@pytest.mark.parametrize(
    ("field", "value", "expected_type"),
    [
        ("state", "present", "BoundValueState"),
        ("origin", "literal", "BoundValueOrigin"),
    ],
)
def test_bound_value_rejects_raw_enum_values(
    field: str,
    value: str,
    expected_type: str,
) -> None:
    kwargs: dict[str, object] = {
        "name": "value",
        "declared_value": "declared",
        "effective_value": "effective",
        "state": BoundValueState.PRESENT,
        "origin": BoundValueOrigin.LITERAL,
    }
    kwargs[field] = value

    with pytest.raises(ValueError, match=expected_type):
        BoundValue(**kwargs)  # type: ignore[arg-type]


def test_bound_step_invocation_freezes_collection_inputs() -> None:
    value = BoundValue(
        name="value",
        declared_value="declared",
        effective_value="effective",
        state=BoundValueState.PRESENT,
        origin=BoundValueOrigin.LITERAL,
    )
    mcp_kwargs = [value]
    skill_inputs = [value]
    failures = []

    invocation = BoundStepInvocation(
        step_name="verify",
        tool_name="run_skill",
        mode=BindingMode.RECIPE,
        skill_name="dry-walkthrough",
        mcp_kwargs=mcp_kwargs,  # type: ignore[arg-type]
        skill_inputs=skill_inputs,  # type: ignore[arg-type]
        failures=failures,  # type: ignore[arg-type]
    )
    mcp_kwargs.clear()
    skill_inputs.clear()

    assert invocation.mcp_kwargs == (value,)
    assert invocation.skill_inputs == (value,)
    assert invocation.failures == ()


def test_bound_step_invocation_rejects_invalid_collection_elements() -> None:
    with pytest.raises(TypeError, match="mcp_kwargs"):
        BoundStepInvocation(
            step_name="verify",
            tool_name="run_skill",
            mode=BindingMode.RECIPE,
            skill_name="dry-walkthrough",
            mcp_kwargs=("not-bound",),  # type: ignore[arg-type]
            skill_inputs=(),
        )


def test_contract_input_order_is_explicit_and_immutable() -> None:
    contract = get_skill_contract("dry-walkthrough", _manifest())
    assert contract is not None
    assert isinstance(contract.inputs, tuple)
    assert tuple(value.name for value in contract.inputs) == (
        "plan_path",
        "issue_url",
        "optional_note",
        "audit_cycle_path",
        "plan_disposition_path",
        "enabled",
        "round",
    )


def test_structured_binding_uses_contract_order_not_mapping_order() -> None:
    values = _required_inputs()
    step = _step(dict(reversed(tuple(values.items()))))
    invocation = bind_step_invocation("verify", step, manifest=_manifest())

    assert invocation.is_valid
    assert tuple(name for name, _value in invocation.canonical_child_invocation) == (
        "plan_path",
        "issue_url",
        "audit_cycle_path",
        "plan_disposition_path",
    )


def test_explicit_empty_manifest_is_not_replaced(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def unexpected_bundled_manifest_load() -> dict[str, object]:
        raise AssertionError("explicit manifest must remain authoritative")

    monkeypatch.setattr(
        binding_module,
        "load_bundled_manifest",
        unexpected_bundled_manifest_load,
    )
    step = _step(_required_inputs())

    invocation = bind_step_invocation("verify", step, manifest={})
    projection = bind_recipe(
        SimpleNamespace(ingredients={}, steps={"verify": step}),
        manifest={},
    )

    assert BindingFailureCode.UNKNOWN_SKILL in {failure.code for failure in invocation.failures}
    projected = projection.for_step("verify")
    assert projected is not None
    assert BindingFailureCode.UNKNOWN_SKILL in {failure.code for failure in projected.failures}


def test_single_inline_input_preserves_multiword_prose_tail() -> None:
    manifest: dict[str, object] = {
        "skills": {
            "investigate": {"inputs": [{"name": "topic", "type": "string", "required": True}]}
        }
    }
    invocation = bind_step_invocation(
        "investigate",
        RecipeStep(
            name="investigate",
            tool="run_skill",
            with_args={
                "skill_command": "/autoskillit:investigate the failing lifecycle",
                "cwd": "/repo",
            },
        ),
        manifest=manifest,
        mode=BindingMode.STANDALONE,
    )

    assert invocation.is_valid
    assert invocation.canonical_child_invocation == (("topic", "the failing lifecycle"),)


def test_optional_absence_does_not_shift_later_values() -> None:
    invocation = bind_step_invocation(
        "verify",
        _step(_required_inputs()),
        manifest=_manifest(),
    )

    optional = invocation.skill_input("optional_note")
    audit_cycle = invocation.skill_input("audit_cycle_path")
    assert optional is not None and optional.state is BoundValueState.ABSENT
    assert audit_cycle is not None
    assert audit_cycle.effective_value == "/tmp/audit/cycle.json"


def test_falsey_values_remain_present() -> None:
    values: dict[str, str | int | float | bool] = _required_inputs()
    values.update(optional_note="", enabled=False, round=0)
    invocation = bind_step_invocation(
        "verify",
        _step(values),
        manifest=_manifest(),
    )

    assert invocation.is_valid
    assert invocation.skill_input("optional_note").effective_value == ""  # type: ignore[union-attr]
    assert invocation.skill_input("enabled").effective_value is False  # type: ignore[union-attr]
    assert invocation.skill_input("round").effective_value == 0  # type: ignore[union-attr]


def test_explicit_empty_declaration_rejects_effective_only_inputs() -> None:
    step = _step(_required_inputs())
    step.declared_with_args = {}

    invocation = bind_step_invocation("verify", step, manifest=_manifest())

    assert not invocation.attested
    undeclared = {
        failure.name
        for failure in invocation.failures
        if "absent from the declaration" in failure.message
    }
    assert undeclared == {"cwd", "skill_command", "skill_inputs"}


def test_spaces_and_shell_metacharacters_round_trip_as_data() -> None:
    values = _required_inputs()
    values["optional_note"] = "a path; $(touch never) && 'quoted value'"
    invocation = bind_step_invocation(
        "verify",
        _step(values),
        manifest=_manifest(),
    )

    assert invocation.is_valid
    assert dict(invocation.canonical_child_invocation)["optional_note"] == values["optional_note"]


def test_unknown_tool_and_skill_namespaces_are_distinct() -> None:
    outer = bind_step_invocation(
        "verify",
        _step(_required_inputs(), inert_sibling="value"),
        manifest=_manifest(),
    )
    inner_values = _required_inputs()
    inner_values["unknown_child"] = "value"
    inner = bind_step_invocation(
        "verify",
        _step(inner_values),
        manifest=_manifest(),
    )

    assert BindingFailureCode.UNKNOWN_TOOL_PARAMETER in {
        failure.code for failure in outer.failures
    }
    assert BindingFailureCode.UNKNOWN_SKILL_INPUT not in {
        failure.code for failure in outer.failures
    }
    assert BindingFailureCode.UNKNOWN_SKILL_INPUT in {failure.code for failure in inner.failures}


@pytest.mark.parametrize(
    ("tool_name", "with_args", "expected_code", "expected_name"),
    [
        (
            "run_cmd",
            {"cwd": "/repo"},
            BindingFailureCode.MISSING_TOOL_PARAMETER,
            "cmd",
        ),
        (
            "run_cmd",
            {"cmd": "pwd"},
            BindingFailureCode.MISSING_TOOL_PARAMETER,
            "cwd",
        ),
        (
            "run_skill",
            {
                "skill_command": "/autoskillit:dry-walkthrough",
                "cwd": "/repo",
                "stale_threshold": "30",
                "skill_inputs": _required_inputs(),
            },
            BindingFailureCode.INVALID_TOOL_PARAMETER_TYPE,
            "stale_threshold",
        ),
        (
            "run_python",
            {"callable": "module:function", "args": "not-an-object"},
            BindingFailureCode.INVALID_TOOL_PARAMETER_TYPE,
            "args",
        ),
        (
            "bulk_close_issues",
            {"issue_numbers": "42", "comment": "done", "cwd": "/repo"},
            BindingFailureCode.INVALID_TOOL_PARAMETER_TYPE,
            "issue_numbers",
        ),
    ],
)
def test_required_and_wire_typed_tool_parameters_reject_invalid_shapes(
    tool_name: str,
    with_args: dict[str, object],
    expected_code: BindingFailureCode,
    expected_name: str,
) -> None:
    invocation = bind_step_invocation(
        "step",
        RecipeStep(name="step", tool=tool_name, with_args=with_args),
        manifest=_manifest(),
    )

    assert any(
        failure.code is expected_code and failure.name == expected_name
        for failure in invocation.failures
    )


def test_missing_and_inline_plus_structured_inputs_reject() -> None:
    missing = _required_inputs()
    del missing["plan_path"]
    missing_invocation = bind_step_invocation(
        "verify",
        _step(missing),
        manifest=_manifest(),
    )
    ambiguous_step = _step(_required_inputs())
    ambiguous_step.with_args["skill_command"] = "/autoskillit:dry-walkthrough /tmp/inline-plan.md"
    ambiguous_step.declared_with_args["skill_command"] = (
        "/autoskillit:dry-walkthrough /tmp/inline-plan.md"
    )
    ambiguous = bind_step_invocation(
        "verify",
        ambiguous_step,
        manifest=_manifest(),
    )

    assert BindingFailureCode.MISSING_SKILL_INPUT in {
        failure.code for failure in missing_invocation.failures
    }
    assert BindingFailureCode.AMBIGUOUS_SKILL_INPUT in {
        failure.code for failure in ambiguous.failures
    }


def test_standalone_binding_cannot_claim_recipe_attestation() -> None:
    invocation = bind_step_invocation(
        "verify",
        _step(_required_inputs()),
        manifest=_manifest(),
        mode=BindingMode.STANDALONE,
    )

    assert invocation.is_valid
    assert not invocation.attested


@pytest.mark.parametrize("use_json", [False, True])
def test_declared_effective_provenance_survives_load_and_prune(
    tmp_path: Path,
    use_json: bool,
) -> None:
    yaml_path = tmp_path / "binding.yaml"
    yaml_path.write_text(
        """
name: binding
description: binding provenance
kitchen_rules: [keep provenance]
ingredients:
  private_root:
    description: private root
    default: /default
    hidden: true
  keep:
    description: keep step
    default: "true"
steps:
  verify:
    tool: run_skill
    with:
      skill_command: /autoskillit:dry-walkthrough
      cwd: /repo
      skill_inputs:
        plan_path: "{{AUTOSKILLIT_TEMP}}/plans/current.md"
        issue_url: https://example.test/issues/42
        audit_cycle_path: "${{ inputs.private_root }}/cycle.json"
        plan_disposition_path: "{{AUTOSKILLIT_TEMP}}/plans/disposition.json"
    skip_when_false: inputs.keep
""".lstrip(),
        encoding="utf-8",
    )
    if use_json:
        parsed = {
            "name": "binding",
            "description": "binding provenance",
            "kitchen_rules": ["keep provenance"],
            "ingredients": {
                "private_root": {
                    "description": "private root",
                    "default": "/default",
                    "hidden": True,
                },
                "keep": {"description": "keep step", "default": "true"},
            },
            "steps": {
                "verify": {
                    "tool": "run_skill",
                    "with": {
                        "skill_command": "/autoskillit:dry-walkthrough",
                        "cwd": "/repo",
                        "skill_inputs": {
                            "plan_path": "{{AUTOSKILLIT_TEMP}}/plans/current.md",
                            "issue_url": "https://example.test/issues/42",
                            "audit_cycle_path": "${{ inputs.private_root }}/cycle.json",
                            "plan_disposition_path": (
                                "{{AUTOSKILLIT_TEMP}}/plans/disposition.json"
                            ),
                        },
                    },
                    "skip_when_false": "inputs.keep",
                }
            },
        }
        json_path = yaml_path.with_suffix(".json")
        json_path.write_text(json.dumps(parsed), encoding="utf-8")
        newer = yaml_path.stat().st_mtime_ns + 1_000_000
        os.utime(json_path, ns=(newer, newer))

    recipe = load_recipe(yaml_path, temp_dir_relpath="/custom temp")
    pre = bind_recipe(
        recipe,
        manifest=_manifest(),
        ingredient_values={"private_root": "/private root", "keep": "true"},
    )
    pruned, _resolutions = _prune_skipped_steps(recipe, {"keep": "true"})
    post = bind_recipe(
        pruned,
        manifest=_manifest(),
        ingredient_values={"private_root": "/private root", "keep": "true"},
    )

    for projection in (pre, post):
        invocation = projection.for_step("verify")
        assert invocation is not None
        plan = invocation.skill_input("plan_path")
        cycle = invocation.skill_input("audit_cycle_path")
        assert plan is not None
        assert plan.declared_value == "{{AUTOSKILLIT_TEMP}}/plans/current.md"
        assert plan.effective_value == "/custom temp/plans/current.md"
        assert plan.template_dependencies == ("AUTOSKILLIT_TEMP",)
        assert cycle is not None
        assert cycle.origin is BoundValueOrigin.TEMPLATE
        assert cycle.input_dependencies == ("private_root",)
        assert cycle.effective_value == "/private root/cycle.json"
