"""Runtime binding tests for producer text passed to typed skill inputs."""

from __future__ import annotations

from pathlib import Path
from typing import Any, NamedTuple

import pytest

from autoskillit.core import (
    BoundValueOrigin,
    BoundValueState,
    InvocationTemplate,
    RecipeSource,
    compute_invocation_template_digest,
    compute_tool_contract_identity,
    get_tool_def,
)
from autoskillit.recipe import (
    RuntimeBindingError,
    bind_recipe,
    bind_runtime_skill_invocation,
    compute_skill_contract_identity,
    get_skill_contract,
    load_bundled_manifest,
)
from autoskillit.recipe.io import load_recipe
from tests._tracked_recipes import tracked_recipe_paths

pytestmark = [pytest.mark.layer("recipe"), pytest.mark.medium]

_ROOT = Path(__file__).resolve().parents[2]
_RECIPES_DIR = _ROOT / "src" / "autoskillit" / "recipes"
_BUNDLED_MANIFEST = load_bundled_manifest()


class _HandoffCase(NamedTuple):
    recipe: Any
    step_key: str
    execution_id: str
    template: InvocationTemplate
    input_name: str


def _has_runtime_dependencies(value: Any) -> bool:
    return bool(value.context_dependencies or value.input_dependencies)


def _mcp_value(invocation: Any, name: str) -> Any:
    matches = [value for value in invocation.mcp_kwargs if value.name == name]
    assert len(matches) == 1, f"expected one compiled MCP kwarg named {name!r}"
    value = matches[0]
    assert value.state is BoundValueState.PRESENT
    return value.effective_value


def _build_template(
    recipe: Any, step_key: str, invocation: Any, manifest: dict[str, Any]
) -> tuple[str, InvocationTemplate]:
    execution_id = f"typed-handoff-{recipe.name}-{step_key}"
    tool_def = get_tool_def(invocation.tool_name)
    assert tool_def is not None
    tool_identity = compute_tool_contract_identity(tool_def)
    assert invocation.skill_name is not None
    skill_identity = compute_skill_contract_identity(invocation.skill_name, manifest=manifest)
    template_digest = compute_invocation_template_digest(
        execution_id=execution_id,
        recipe_name=recipe.name,
        content_hash=recipe.content_hash,
        composite_hash=recipe.content_hash,
        invocation=invocation,
        tool_contract_identity=tool_identity,
        skill_contract_identity=skill_identity,
    )
    return execution_id, InvocationTemplate(
        invocation=invocation,
        tool_contract_identity=tool_identity,
        skill_contract_identity=skill_identity,
        template_digest=template_digest,
    )


def _present_dynamic_context_integer_inputs(
    invocation: Any, manifest: dict[str, Any]
) -> list[Any]:
    assert invocation.skill_name is not None
    contract = get_skill_contract(invocation.skill_name, manifest)
    assert contract is not None
    input_defs = {input_def.name: input_def for input_def in contract.inputs}
    return [
        value
        for value in invocation.skill_inputs
        if value.state is BoundValueState.PRESENT
        and value.origin is BoundValueOrigin.CONTEXT
        and _has_runtime_dependencies(value)
        and input_defs[value.name].type == "integer"
    ]


def _handoff_cases_for_recipe(recipe: Any, manifest: dict[str, Any]) -> list[_HandoffCase]:
    projection = bind_recipe(recipe, manifest=manifest)
    cases = []
    for step_key, invocation in projection.invocations.items():
        if invocation.tool_name != "run_skill" or not invocation.attested:
            continue
        integer_inputs = _present_dynamic_context_integer_inputs(invocation, manifest)
        if not integer_inputs:
            continue
        execution_id, template = _build_template(recipe, step_key, invocation, manifest)
        cases.extend(
            _HandoffCase(recipe, step_key, execution_id, template, value.name)
            for value in integer_inputs
        )
    return cases


def _integer_handoff_cases() -> list[_HandoffCase]:
    cases = []
    for path in tracked_recipe_paths(_ROOT, source=RecipeSource.BUILTIN):
        cases.extend(_handoff_cases_for_recipe(load_recipe(path), _BUNDLED_MANIFEST))
    return cases


_HANDOFF_CASES = _integer_handoff_cases()


def _native_sample(input_def: Any) -> str | int | bool:
    if input_def.absence_value is not None:
        return input_def.absence_value
    for sample in ("fixture", 1, False):
        if input_def.accepts(sample):
            return sample
    raise AssertionError(f"no native sample for skill input {input_def.name!r}")


def _runtime_skill_inputs(
    case: _HandoffCase, overrides: dict[str, str | int | bool]
) -> dict[str, Any]:
    invocation = case.template.invocation
    assert invocation.skill_name is not None
    contract = get_skill_contract(invocation.skill_name, _BUNDLED_MANIFEST)
    assert contract is not None
    input_defs = {input_def.name: input_def for input_def in contract.inputs}
    skill_inputs = {}
    for value in invocation.skill_inputs:
        if value.state is not BoundValueState.PRESENT:
            continue
        if value.name in overrides:
            actual = overrides[value.name]
        elif _has_runtime_dependencies(value):
            actual = _native_sample(input_defs[value.name])
        else:
            actual = value.effective_value
        skill_inputs[value.name] = actual
    return skill_inputs


def _actual_mcp_kwargs(case: _HandoffCase) -> dict[str, Any]:
    invocation = case.template.invocation
    actual_mcp_kwargs = {}
    for value in invocation.mcp_kwargs:
        if value.state is not BoundValueState.PRESENT:
            continue
        actual_mcp_kwargs[value.name] = (
            "fixture" if _has_runtime_dependencies(value) else value.effective_value
        )
    step_name = _mcp_value(invocation, "step_name")
    assert isinstance(step_name, str)
    actual_mcp_kwargs["step_name"] = step_name
    actual_mcp_kwargs["recipe_execution_id"] = case.execution_id
    actual_mcp_kwargs["invocation_template_digest"] = case.template.template_digest
    return actual_mcp_kwargs


def _bind(case: _HandoffCase, skill_inputs: dict[str, Any]) -> tuple[tuple[str, Any], ...]:
    invocation = case.template.invocation
    skill_command = _mcp_value(invocation, "skill_command")
    step_name = _mcp_value(invocation, "step_name")
    assert isinstance(skill_command, str)
    assert isinstance(step_name, str)
    return bind_runtime_skill_invocation(
        case.template,
        execution_id=case.execution_id,
        step_name=step_name,
        skill_command=skill_command,
        skill_inputs=skill_inputs,
        actual_mcp_kwargs=_actual_mcp_kwargs(case),
    )


def _producer_expression(recipe: Any, variable: str) -> str:
    expressions = []
    for step in recipe.steps.values():
        for captures in (step.capture, step.capture_list):
            if variable in captures:
                expressions.append(captures[variable].from_)
    assert len(expressions) == 1, (
        f"{recipe.name}: expected one capture/capture_list producer for {variable!r}, "
        f"found {len(expressions)}"
    )
    expression = expressions[0]
    assert isinstance(expression, str)
    filters = [part.strip() for part in expression.removesuffix("}}").split("|")[1:]]
    assert all(filter_name == "trim" for filter_name in filters), (
        f"{recipe.name}: unsupported producer filter in {expression!r}"
    )
    return expression


def _producer_text(recipe: Any, variable: str) -> str:
    expression = _producer_expression(recipe, variable)
    emitted = "31\n" if "result.stdout" in expression else "31"
    if "trim" in (part.strip() for part in expression.removesuffix("}}").split("|")[1:]):
        emitted = emitted.strip()
    return emitted


@pytest.mark.parametrize(
    "case",
    _HANDOFF_CASES,
    ids=lambda case: f"{case.recipe.name}:{case.step_key}:{case.input_name}",
)
def test_integer_handoffs_bind_producer_text(case: _HandoffCase) -> None:
    bound_input = case.template.invocation.skill_input(case.input_name)
    assert bound_input is not None
    producer_text = _producer_text(case.recipe, bound_input.context_dependencies[0])
    bound = _bind(case, _runtime_skill_inputs(case, {case.input_name: producer_text}))
    native = _bind(case, _runtime_skill_inputs(case, {case.input_name: 31}))

    assert dict(bound)[case.input_name] == 31
    assert dict(bound) == dict(native)
    malformed = _runtime_skill_inputs(case, {case.input_name: "031"})
    with pytest.raises(RuntimeBindingError) as exc_info:
        _bind(case, malformed)
    assert exc_info.value.code == "recipe_execution_input_type"


def test_issue_named_integer_handoffs_are_covered() -> None:
    actual_pairs = {(case.recipe.name, case.step_key) for case in _HANDOFF_CASES}
    expected_pairs = {
        ("implementation", "review_pr"),
        ("implementation", "resolve_review"),
        ("implementation-groups", "review_pr"),
        ("implementation-groups", "resolve_review"),
        ("remediation", "review_pr"),
        ("remediation", "resolve_review"),
    }
    assert expected_pairs <= actual_pairs
    for recipe_name in ("research", "research-review", "merge-prs"):
        assert any(name == recipe_name for name, _step in actual_pairs), recipe_name


def test_reverse_direction_text_inputs_render_canonically() -> None:
    manifest = _BUNDLED_MANIFEST
    recipe = load_recipe(_RECIPES_DIR / "implementation.yaml")
    invocation = bind_recipe(recipe, manifest=manifest).for_step("prepare_pr")
    execution_id, template = _build_template(recipe, "prepare_pr", invocation, manifest)
    case = _HandoffCase(recipe, "prepare_pr", execution_id, template, "closing_issue")
    values = {value.name: value for value in invocation.skill_inputs}
    assert values["closing_issue"].absence_value == ""
    assert values["arch_lenses"].origin is BoundValueOrigin.RECIPE_INPUT

    closing_issue = _bind(case, _runtime_skill_inputs(case, {"closing_issue": 1}))
    assert dict(closing_issue)["closing_issue"] == "1"
    with pytest.raises(RuntimeBindingError) as exc_info:
        _bind(case, _runtime_skill_inputs(case, {"closing_issue": False}))
    assert exc_info.value.code == "recipe_execution_input_type"

    arch_lenses = _bind(case, _runtime_skill_inputs(case, {"arch_lenses": False}))
    assert dict(arch_lenses)["arch_lenses"] == "false"
