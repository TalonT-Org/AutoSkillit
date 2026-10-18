"""Tests for compiling per-item skill inputs from recipe dispatch declarations."""

from __future__ import annotations

import pytest

from autoskillit.core import (
    BindingFailureCode,
    BoundStepInvocation,
    BoundValue,
    BoundValueOrigin,
)
from autoskillit.recipe import _binding_input
from autoskillit.recipe._binding import bind_step_invocation
from autoskillit.recipe.contracts._contracts_manifest import load_bundled_manifest
from autoskillit.recipe.rules import rules_inputs
from autoskillit.recipe.schema import RecipeStep

pytestmark = [pytest.mark.layer("recipe"), pytest.mark.small]

_DISPATCH_ITEMS = "${{ context.phase_ids }}"
_SKILL_NAME = "planner-elaborate-phase"
_NO_DISPATCH = object()


def _structured_skill_inputs(
    target_phase_id: object,
    *,
    output_dir: object = "${{ context.planner_dir }}/phases",
) -> dict[str, object]:
    return {
        "plan_snapshot_path": "${{ context.plan_snapshot_path }}",
        "target_phase_id": target_phase_id,
        "output_dir": output_dir,
    }


def _step(
    *,
    phase_token: str = "{phase_id}",
    dispatch_items: object = _DISPATCH_ITEMS,
    skill_inputs: dict[str, object] | None = None,
    output_dir: str = "${{ context.planner_dir }}",
) -> RecipeStep:
    skill_command = f"/autoskillit:{_SKILL_NAME}"
    if skill_inputs is None:
        skill_command += (
            f" ${{{{ context.plan_snapshot_path }}}} {phase_token}"
            " ${{ context.planner_dir }}/phases"
        )
    with_args: dict[str, object] = {
        "skill_command": skill_command,
        "cwd": "${{ inputs.source_dir }}",
        "output_dir": output_dir,
    }
    if dispatch_items is not _NO_DISPATCH:
        with_args["dispatch_items"] = dispatch_items
    if skill_inputs is not None:
        with_args["skill_inputs"] = skill_inputs
    return RecipeStep(name="elaborate_phases", tool="run_skill", with_args=with_args)


def _bind(step: RecipeStep) -> BoundStepInvocation:
    return bind_step_invocation("elaborate_phases", step, manifest=load_bundled_manifest())


def _assert_dispatch_slot(
    invocation: BoundStepInvocation, *, origin: BoundValueOrigin
) -> BoundValue:
    phase_input = invocation.skill_input("target_phase_id")
    assert phase_input is not None
    assert phase_input.origin is origin
    assert phase_input.is_present
    return phase_input


@pytest.mark.parametrize(
    "phase_token",
    ["{phase_id}", '"{ phase_id }"', "{phase-id}", "{phase.id}"],
)
def test_inline_dispatch_step_binds_one_dispatch_item(phase_token: str) -> None:
    invocation = _bind(_step(phase_token=phase_token))

    assert invocation.is_valid, invocation.failures
    assert invocation.attested
    dispatch_inputs = [
        value
        for value in invocation.skill_inputs
        if value.is_present and value.origin is BoundValueOrigin.DISPATCH_ITEM
    ]
    assert len(dispatch_inputs) == 1
    phase_input = _assert_dispatch_slot(invocation, origin=BoundValueOrigin.DISPATCH_ITEM)
    assert phase_input.context_dependencies == ("phase_ids",)
    assert phase_input.is_dynamic
    assert dispatch_inputs == [phase_input]

    skill_command = next(value for value in invocation.mcp_kwargs if value.name == "skill_command")
    assert "phase_ids" in skill_command.context_dependencies


def test_structured_dispatch_step_binds_identically() -> None:
    invocation = _bind(_step(skill_inputs=_structured_skill_inputs("{phase_id}")))

    assert invocation.is_valid, invocation.failures
    assert invocation.attested
    phase_input = _assert_dispatch_slot(invocation, origin=BoundValueOrigin.DISPATCH_ITEM)
    assert phase_input.context_dependencies == ("phase_ids",)
    assert phase_input.is_dynamic


def test_inputs_producer_is_accepted() -> None:
    invocation = _bind(_step(dispatch_items="${{ inputs.phase_ids }}"))

    assert invocation.is_valid, invocation.failures
    phase_input = _assert_dispatch_slot(invocation, origin=BoundValueOrigin.DISPATCH_ITEM)
    assert phase_input.input_dependencies == ("phase_ids",)
    assert phase_input.is_dynamic


def test_unrelated_literal_brace_kwarg_stays_static() -> None:
    step = _step(output_dir="/tmp/{build_id}/out.json")
    invocation = _bind(step)
    baseline = _bind(_step(dispatch_items=_NO_DISPATCH, output_dir="/tmp/{build_id}/out.json"))

    assert invocation.attested
    output = next(value for value in invocation.mcp_kwargs if value.name == "output_dir")
    assert output == next(value for value in baseline.mcp_kwargs if value.name == "output_dir")
    assert output.origin is BoundValueOrigin.LITERAL
    assert not output.is_dynamic


def test_literal_inline_command_receives_dispatch_dependency() -> None:
    invocation = _bind(
        RecipeStep(
            name="elaborate_phases",
            tool="run_skill",
            with_args={
                "skill_command": "/autoskillit:planner-elaborate-phase /snapshot {phase_id} /out",
                "cwd": "/repo",
                "dispatch_items": _DISPATCH_ITEMS,
            },
        )
    )

    assert invocation.attested
    command = next(value for value in invocation.mcp_kwargs if value.name == "skill_command")
    assert command.origin is BoundValueOrigin.TEMPLATE
    assert command.context_dependencies == ("phase_ids",)


def test_step_without_dispatch_items_is_unchanged() -> None:
    invocation = _bind(_step(dispatch_items=_NO_DISPATCH))

    assert invocation.is_valid, invocation.failures
    phase_input = _assert_dispatch_slot(invocation, origin=BoundValueOrigin.LITERAL)
    assert phase_input.declared_value == "{phase_id}"
    assert phase_input.context_dependencies == ()
    assert phase_input.input_dependencies == ()


@pytest.mark.parametrize(
    ("dispatch_items", "skill_inputs", "output_dir", "producer_contexts", "expected_reason"),
    [
        pytest.param(
            "P1,P2",
            None,
            "${{ context.planner_dir }}",
            (),
            "must be one exact",
            id="literal-producer",
        ),
        pytest.param(
            "${{ context.a }},${{ context.b }}",
            None,
            "${{ context.planner_dir }}",
            ("a", "b"),
            "must be one exact",
            id="non-exact-producer",
        ),
        pytest.param(
            _DISPATCH_ITEMS,
            _structured_skill_inputs("{phase_id}", output_dir="{other}"),
            "${{ context.planner_dir }}",
            ("phase_ids",),
            "requires exactly one whole-value {placeholder}",
            id="distinct-item-placeholders",
        ),
        pytest.param(
            _DISPATCH_ITEMS,
            _structured_skill_inputs("{phase_id}", output_dir="{phase_id}"),
            "${{ context.planner_dir }}",
            ("phase_ids",),
            "requires exactly one whole-value {placeholder}",
            id="same-placeholder-in-two-inputs",
        ),
        pytest.param(
            _DISPATCH_ITEMS,
            _structured_skill_inputs("{phase_id}/{phase_id}"),
            "${{ context.planner_dir }}",
            ("phase_ids",),
            "requires exactly one whole-value {placeholder}",
            id="repeated-placeholder-in-one-input",
        ),
        pytest.param(
            _DISPATCH_ITEMS,
            _structured_skill_inputs("/out/{phase_id}/x.json"),
            "${{ context.planner_dir }}",
            ("phase_ids",),
            "requires exactly one whole-value {placeholder}",
            id="embedded-item-value",
        ),
        pytest.param(
            _DISPATCH_ITEMS,
            _structured_skill_inputs("{phase_id"),
            "${{ context.planner_dir }}",
            ("phase_ids",),
            "requires exactly one whole-value {placeholder}",
            id="unterminated-item-value",
        ),
        pytest.param(
            _DISPATCH_ITEMS,
            _structured_skill_inputs("P1"),
            "/tmp/{build_id}/out.json",
            ("phase_ids",),
            "requires exactly one whole-value {placeholder}",
            id="mcp-path-is-not-an-item-input",
        ),
        pytest.param(
            _DISPATCH_ITEMS,
            None,
            "/tmp/{phase_id}/out.json",
            ("phase_ids",),
            "in MCP argument 'output_dir' must not occur outside the inline skill command",
            id="item-token-also-in-mcp-kwarg",
        ),
    ],
)
def test_invalid_dispatch_declaration_is_not_attested(
    dispatch_items: object,
    skill_inputs: dict[str, object] | None,
    output_dir: str,
    producer_contexts: tuple[str, ...],
    expected_reason: str,
) -> None:
    invocation = _bind(
        _step(
            dispatch_items=dispatch_items,
            skill_inputs=skill_inputs,
            output_dir=output_dir,
        )
    )
    baseline = _bind(
        _step(
            dispatch_items=_NO_DISPATCH,
            skill_inputs=skill_inputs,
            output_dir=output_dir,
        )
    )

    failure = next(
        failure
        for failure in invocation.failures
        if failure.code is BindingFailureCode.INVALID_DISPATCH_ITEMS
    )
    assert failure.name == "dispatch_items"
    assert "dispatch_items" in failure.message
    assert expected_reason in failure.message
    assert not invocation.attested
    assert invocation.skill_inputs == baseline.skill_inputs
    assert invocation.mcp_kwargs == baseline.mcp_kwargs
    assert invocation.failures == (*baseline.failures, failure)
    assert invocation.skill_input("plan_snapshot_path") is not None
    assert invocation.skill_input("target_phase_id") is not None
    assert invocation.skill_input("output_dir") is not None
    for value in invocation.skill_inputs:
        assert not (set(producer_contexts) & set(value.context_dependencies))


@pytest.mark.parametrize(
    ("phase_token", "dispatch", "unresolved"),
    [
        ("{phase_id}", True, True),
        ("{ phase_id }", True, True),
        ("{phase-id}", True, True),
        ("{phase.id}", True, True),
        ("{{phase_id}}", False, True),
        ("{{phase_id}", False, True),
        ("{phase_id}}", False, True),
        ("${{ context.phase_ids }}", False, False),
    ],
)
def test_placeholder_grammar_distinguishes_dispatch_tokens(
    phase_token: str, dispatch: bool, unresolved: bool
) -> None:
    assert bool(_binding_input._DISPATCH_TOKEN_RE.fullmatch(phase_token)) is dispatch
    assert bool(rules_inputs._WHOLE_VALUE_PLACEHOLDER_RE.fullmatch(phase_token)) is unresolved


@pytest.mark.parametrize("value", ["{AUTOSKILLIT_TEMP}", "{{AUTOSKILLIT_TEMP}}"])
def test_dispatch_token_excludes_reserved_autoskillit_names(value: str) -> None:
    assert _binding_input._DISPATCH_TOKEN_RE.search(value) is None


@pytest.mark.parametrize("value", ["${VAR}", "${{ context.phase_ids }}", "{}", '{"a": 1}'])
def test_dispatch_token_excludes_non_item_syntax(value: str) -> None:
    assert _binding_input._DISPATCH_TOKEN_RE.search(value) is None
