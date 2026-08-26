"""Pure compiler for recipe tool and child-skill invocations."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from autoskillit.core import (
    BindingFailure,
    BindingFailureCode,
    BindingMode,
    BoundScalar,
    BoundStepInvocation,
    BoundValue,
    BoundValueOrigin,
    BoundValueState,
    InvocationTemplate,
    RecipeBindingProjection,
    ToolDef,
    ToolParamDef,
    ToolParamRole,
    ToolWireType,
    get_tool_def,
    resolve_skill_name,
    runtime_exempt_param_names,
)
from autoskillit.recipe._binding_input import (
    _AUTOSKILLIT_TEMPLATE_RE,
    _CONTEXT_REF_RE,
    _INPUT_REF_RE,
    _bound_value,
    _failure,
    _inline_skill_inputs,
    _is_scalar,
    _resolve_hidden_value,
    _structured_skill_inputs,
    _tokenize_skill_command,
)
from autoskillit.recipe._contracts_manifest import (
    compute_skill_contract_identity as _compute_skill_contract_identity,
)
from autoskillit.recipe._contracts_manifest import (
    get_callable_contract,
    get_skill_contract,
    load_bundled_manifest,
)
from autoskillit.recipe.schema import RecipeStep

__all__ = [
    "RuntimeBindingError",
    "bind_recipe",
    "bind_runtime_skill_invocation",
    "bind_step_invocation",
    "compute_skill_contract_identity",
]


class RuntimeBindingError(ValueError):
    """Stable recipe-domain rejection for one runtime invocation binding."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def compute_skill_contract_identity(
    skill_name: str,
    *,
    manifest: dict[str, Any] | None = None,
) -> str:
    """Hash a skill contract through this module's patchable resolver seam."""
    return _compute_skill_contract_identity(
        skill_name,
        manifest=manifest,
        contract_resolver=get_skill_contract,
        manifest_loader=load_bundled_manifest,
    )


def _structured_dependencies(
    value: object,
) -> tuple[tuple[str, ...], tuple[str, ...], tuple[str, ...]]:
    context: list[str] = []
    inputs: list[str] = []
    templates: list[str] = []

    def visit(item: object) -> None:
        if isinstance(item, str):
            context.extend(_CONTEXT_REF_RE.findall(item))
            inputs.extend(_INPUT_REF_RE.findall(item))
            templates.extend(_AUTOSKILLIT_TEMPLATE_RE.findall(item))
        elif isinstance(item, Mapping):
            for key, nested in item.items():
                visit(key)
                visit(nested)
        elif isinstance(item, (list, tuple)):
            for nested in item:
                visit(nested)

    visit(value)
    return (
        tuple(dict.fromkeys(context)),
        tuple(dict.fromkeys(inputs)),
        tuple(dict.fromkeys(templates)),
    )


def _bound_structured_value(name: str, declared: object, effective: object) -> BoundValue:
    context_dependencies, input_dependencies, template_dependencies = _structured_dependencies(
        declared
    )
    origin = (
        BoundValueOrigin.TEMPLATE
        if context_dependencies or input_dependencies or template_dependencies
        else BoundValueOrigin.LITERAL
    )
    return BoundValue(
        name=name,
        declared_value=declared,
        effective_value=effective,
        state=BoundValueState.PRESENT,
        origin=origin,
        context_dependencies=context_dependencies,
        input_dependencies=input_dependencies,
        template_dependencies=template_dependencies,
    )


def _snapshot_json_value(value: object) -> object:
    if value is None or isinstance(value, (str, int, bool)):
        return value
    if isinstance(value, float):
        raise ValueError("floating-point values are outside the canonical JSON profile")
    if isinstance(value, Mapping):
        if any(not isinstance(key, str) for key in value):
            raise ValueError("object keys must be strings")
        return {key: _snapshot_json_value(nested) for key, nested in value.items()}
    if isinstance(value, (list, tuple)):
        return tuple(_snapshot_json_value(nested) for nested in value)
    raise ValueError(f"unsupported nested value {type(value).__name__}")


def _wire_value_is_valid(value: object, param: ToolParamDef) -> bool:
    match param.wire_type:
        case ToolWireType.STRING:
            return isinstance(value, str)
        case ToolWireType.INTEGER:
            return isinstance(value, int) and not isinstance(value, bool)
        case ToolWireType.NUMBER:
            return isinstance(value, int) and not isinstance(value, bool)
        case ToolWireType.BOOLEAN:
            return isinstance(value, bool)
        case ToolWireType.SCALAR:
            return _is_scalar(value)
        case ToolWireType.OBJECT:
            return isinstance(value, Mapping)
        case ToolWireType.ARRAY:
            return isinstance(value, (list, tuple))


def bind_step_invocation(
    step_name: str,
    step: Any,
    *,
    manifest: dict[str, Any] | None = None,
    ingredient_values: Mapping[str, BoundScalar] | None = None,
    hidden_inputs: frozenset[str] = frozenset(),
    mode: BindingMode = BindingMode.RECIPE,
) -> BoundStepInvocation:
    """Compile one recipe step without mutating its source model."""

    tool_name = step.tool or ""
    effective_with: Mapping[str, object] = step.with_args or {}
    declared_candidate: Mapping[str, object] | None = getattr(step, "declared_with_args", None)
    declared_with = effective_with if declared_candidate is None else declared_candidate
    tool_def = get_tool_def(tool_name)
    if tool_def is None:
        failure = _failure(
            BindingFailureCode.UNKNOWN_TOOL,
            step_name,
            tool_name,
            f"tool {tool_name!r} is not present in the canonical registry",
        )
        return BoundStepInvocation(step_name, tool_name, mode, None, (), (), (failure,))

    failures: list[BindingFailure] = []
    active_manifest = manifest if manifest is not None else load_bundled_manifest()
    undeclared_effective_params = frozenset(effective_with) - frozenset(declared_with)
    for name in sorted(undeclared_effective_params):
        failures.append(
            _failure(
                BindingFailureCode.UNKNOWN_TOOL_PARAMETER,
                step_name,
                name,
                f"effective tool parameter {name!r} is absent from the declaration",
            )
        )
    authorable_params = tool_def.param_set
    if tool_name == "run_python":
        callable_name = effective_with.get("callable")
        callable_contract = (
            get_callable_contract(callable_name, active_manifest)
            if isinstance(callable_name, str)
            else None
        )
        if callable_contract is not None:
            authorable_params |= frozenset(
                input_def.name for input_def in callable_contract.inputs
            )
        if isinstance(callable_name, str):
            # Outer run_python keys are the callable's argument channel. A
            # manifest contract describes output capture, but all callable
            # arguments are packed into the run_python handler's canonical
            # ``args`` object rather than becoming MCP parameters.
            authorable_params |= frozenset(declared_with)
    unknown_params = frozenset(declared_with) - authorable_params
    for name in sorted(unknown_params):
        failures.append(
            _failure(
                BindingFailureCode.UNKNOWN_TOOL_PARAMETER,
                step_name,
                name,
                f"tool {tool_name!r} has no parameter named {name!r}",
            )
        )

    ingredients = ingredient_values or {}
    mcp_kwargs: list[BoundValue] = []
    for param in tool_def.params:
        if param.structured_skill_inputs:
            continue
        if param.name not in declared_with:
            if param.required:
                failures.append(
                    _failure(
                        BindingFailureCode.MISSING_TOOL_PARAMETER,
                        step_name,
                        param.name,
                        f"required tool parameter {param.name!r} is absent",
                    )
                )
            continue
        declared = declared_with[param.name]
        effective = effective_with.get(param.name, declared)
        if param.wire_type in {ToolWireType.OBJECT, ToolWireType.ARRAY}:
            if not _wire_value_is_valid(declared, param) or not _wire_value_is_valid(
                effective, param
            ):
                failures.append(
                    _failure(
                        BindingFailureCode.INVALID_TOOL_PARAMETER_TYPE,
                        step_name,
                        param.name,
                        f"tool parameter {param.name!r} expects {param.wire_type.value!r}",
                    )
                )
                continue
            try:
                declared_snapshot = _snapshot_json_value(declared)
                effective_snapshot = _snapshot_json_value(effective)
            except ValueError as exc:
                failures.append(
                    _failure(
                        BindingFailureCode.INVALID_TOOL_PARAMETER_TYPE,
                        step_name,
                        param.name,
                        f"tool parameter {param.name!r} is not canonical JSON: {exc}",
                    )
                )
                continue
            mcp_kwargs.append(
                _bound_structured_value(
                    param.name,
                    declared_snapshot,
                    effective_snapshot,
                )
            )
            continue
        if not _is_scalar(declared) or not _is_scalar(effective):
            failures.append(
                _failure(
                    BindingFailureCode.INVALID_TOOL_PARAMETER_TYPE,
                    step_name,
                    param.name,
                    f"tool parameter {param.name!r} must be a strict scalar",
                )
            )
            continue
        resolved = _resolve_hidden_value(
            declared,
            effective,
            hidden_inputs=hidden_inputs,
            ingredient_values=ingredients,
        )
        value = _bound_value(param.name, declared, resolved)
        mcp_kwargs.append(value)
        unresolved = bool(value.context_dependencies or value.input_dependencies)
        if not unresolved and not _wire_value_is_valid(resolved, param):
            failures.append(
                _failure(
                    BindingFailureCode.INVALID_TOOL_PARAMETER_TYPE,
                    step_name,
                    param.name,
                    f"tool parameter {param.name!r} expects {param.wire_type.value!r}",
                )
            )

    if tool_name != "run_skill":
        return BoundStepInvocation(
            step_name,
            tool_name,
            mode,
            None,
            tuple(mcp_kwargs),
            (),
            tuple(failures),
        )

    declared_command = declared_with.get("skill_command")
    effective_command = effective_with.get("skill_command", declared_command)
    if not isinstance(declared_command, str) or not isinstance(effective_command, str):
        failures.append(
            _failure(
                BindingFailureCode.INVALID_SKILL_COMMAND,
                step_name,
                "skill_command",
                "run_skill.skill_command must be a string",
            )
        )
        return BoundStepInvocation(
            step_name,
            tool_name,
            mode,
            None,
            tuple(mcp_kwargs),
            (),
            tuple(failures),
        )

    skill_name = resolve_skill_name(effective_command)
    declared_structured = declared_with.get("skill_inputs")
    effective_structured = effective_with.get("skill_inputs", declared_structured)

    contract = get_skill_contract(skill_name, active_manifest) if skill_name else None
    if skill_name is None or contract is None:
        if mode is BindingMode.RECIPE and not declared_command.lstrip().startswith("/"):
            # LLM-orchestrated recipe steps may use a non-executable placeholder
            # command while their note defines a bounded fan-out of tool calls.
            return BoundStepInvocation(
                step_name,
                tool_name,
                mode,
                None,
                tuple(mcp_kwargs),
                (),
                tuple(failures),
            )
        if mode is BindingMode.RECIPE and "{" in declared_command:
            generic_inputs: list[BoundValue] = []
            if isinstance(declared_structured, Mapping) and isinstance(
                effective_structured, Mapping
            ):
                for name, declared in declared_structured.items():
                    effective = effective_structured.get(name, declared)
                    if isinstance(name, str) and _is_scalar(declared) and _is_scalar(effective):
                        generic_inputs.append(_bound_value(name, declared, effective))
            return BoundStepInvocation(
                step_name,
                tool_name,
                mode,
                None,
                tuple(mcp_kwargs),
                tuple(generic_inputs),
                tuple(failures),
            )
        failures.append(
            _failure(
                BindingFailureCode.UNKNOWN_SKILL,
                step_name,
                skill_name or "skill_command",
                "selected skill has no canonical contract",
            )
        )
        return BoundStepInvocation(
            step_name,
            tool_name,
            mode,
            skill_name,
            tuple(mcp_kwargs),
            (),
            tuple(failures),
        )

    try:
        has_inline_args = bool(_tokenize_skill_command(declared_command)[1:])
    except ValueError:
        has_inline_args = True
    if declared_structured is not None and has_inline_args:
        failures.append(
            _failure(
                BindingFailureCode.AMBIGUOUS_SKILL_INPUT,
                step_name,
                "skill_inputs",
                "run_skill may not mix inline arguments with structured skill_inputs",
            )
        )
        skill_inputs = tuple(BoundValue.absent(item.name) for item in contract.inputs)
    elif declared_structured is not None:
        if not isinstance(declared_structured, Mapping) or not isinstance(
            effective_structured, Mapping
        ):
            failures.append(
                _failure(
                    BindingFailureCode.INVALID_SKILL_INPUT_TYPE,
                    step_name,
                    "skill_inputs",
                    "run_skill.skill_inputs must be a mapping",
                )
            )
            skill_inputs = ()
        else:
            skill_inputs, skill_failures = _structured_skill_inputs(
                step_name=step_name,
                declared_values=declared_structured,
                effective_values=effective_structured,
                contract=contract,
                hidden_inputs=hidden_inputs,
                ingredient_values=ingredients,
                optional_context_refs=frozenset(step.optional_context_refs),
            )
            failures.extend(skill_failures)
    else:
        skill_inputs, skill_failures = _inline_skill_inputs(
            step_name=step_name,
            declared_command=declared_command,
            effective_command=effective_command,
            contract=contract,
        )
        failures.extend(skill_failures)

    return BoundStepInvocation(
        step_name=step_name,
        tool_name=tool_name,
        mode=mode,
        skill_name=skill_name,
        mcp_kwargs=tuple(mcp_kwargs),
        skill_inputs=skill_inputs,
        failures=tuple(failures),
    )


def bind_recipe(
    recipe: Any,
    *,
    manifest: dict[str, Any] | None = None,
    ingredient_values: Mapping[str, BoundScalar] | None = None,
    mode: BindingMode = BindingMode.RECIPE,
) -> RecipeBindingProjection:
    """Compile a fresh immutable projection for the supplied recipe snapshot."""

    values: dict[str, BoundScalar] = {}
    for name, ingredient in (recipe.ingredients or {}).items():
        default = getattr(ingredient, "default", None)
        if _is_scalar(default):
            values[name] = default
    if ingredient_values:
        values.update(ingredient_values)
    hidden_inputs = frozenset(
        name
        for name, ingredient in (recipe.ingredients or {}).items()
        if getattr(ingredient, "hidden", False)
    )
    active_manifest = manifest if manifest is not None else load_bundled_manifest()
    invocations = {
        step_name: bind_step_invocation(
            step_name,
            step,
            manifest=active_manifest,
            ingredient_values=values,
            hidden_inputs=hidden_inputs,
            mode=mode,
        )
        for step_name, step in recipe.steps.items()
        if step.tool is not None
    }
    return RecipeBindingProjection(invocations)


def _is_dynamic_binding(value: BoundValue) -> bool:
    return bool(value.context_dependencies or value.input_dependencies)


def _undeclared_runtime_param_message(tool_def: ToolDef, undeclared_names: list[str]) -> str:
    """Denial text for undeclared non-empty runtime tool parameters.

    Keeps the original generic shape naming every undeclared parameter, and
    appends an actionable remedy for any EXECUTION_TUNING name: it is
    server-resolved from the recipe step, must be omitted from the call,
    and declaring it under the step's ``with:`` block is the only per-step
    override channel — with the equality-pinning caveat that channel carries
    (see ``bind_runtime_skill_invocation``'s static-value check). Every other
    role keeps the plain generic shape.
    """
    message = (
        f"runtime tool parameters are absent from the compiled template: {undeclared_names!r}"
    )
    tuning_names = [
        name
        for name in undeclared_names
        if (param := tool_def.param_def(name)) is not None
        and param.role is ToolParamRole.EXECUTION_TUNING
    ]
    if tuning_names:
        remedies = "; ".join(
            f"{name!r} is server-resolved from the recipe step — omit it, or declare "
            f"{name!r} under this step's with: block for a per-step override (a static "
            "with: value admits only that exact value; only a dynamically-bound value "
            "varies per call)"
            for name in tuning_names
        )
        message = f"{message}; {remedies}"
    return message


def bind_runtime_skill_invocation(
    template: InvocationTemplate,
    *,
    execution_id: str,
    step_name: str,
    skill_command: str,
    skill_inputs: Mapping[str, BoundScalar] | None,
    actual_mcp_kwargs: Mapping[str, BoundScalar],
) -> tuple[tuple[str, BoundScalar], ...]:
    """Bind runtime values against one immutable compiled recipe template."""
    invocation = template.invocation
    if resolve_skill_name(skill_command) != invocation.skill_name:
        raise RuntimeBindingError(
            "recipe_execution_skill_mismatch",
            "runtime skill identity differs from the compiled template",
        )
    compiled_mcp_names = frozenset(value.name for value in invocation.mcp_kwargs)
    # Bind the three protocol parameters against their expected attestation values.
    protocol_mcp_values = {
        "step_name": step_name,
        "recipe_execution_id": execution_id,
        "invocation_template_digest": template.template_digest,
    }
    for name, expected in protocol_mcp_values.items():
        if name in actual_mcp_kwargs and actual_mcp_kwargs[name] != expected:
            raise RuntimeBindingError(
                "recipe_execution_tool_shape",
                f"attestation parameter {name!r} differs from the active invocation",
            )
    bound_tool_def = get_tool_def(invocation.tool_name)
    if bound_tool_def is None:
        raise RuntimeBindingError(
            "recipe_execution_tool_shape",
            f"no canonical tool definition for {invocation.tool_name!r}",
        )
    runtime_admitted_names = compiled_mcp_names | runtime_exempt_param_names(bound_tool_def)
    undeclared_effective_names = sorted(
        name
        for name, value in actual_mcp_kwargs.items()
        if name not in runtime_admitted_names and value != ""
    )
    if undeclared_effective_names:
        raise RuntimeBindingError(
            "recipe_execution_tool_shape",
            _undeclared_runtime_param_message(bound_tool_def, undeclared_effective_names),
        )
    manifest = load_bundled_manifest()
    contract = get_skill_contract(invocation.skill_name or "", manifest)
    if contract is None:
        raise RuntimeBindingError(
            "recipe_execution_contract_unavailable",
            "the compiled skill contract is unavailable at runtime",
        )
    runtime_skill_identity = compute_skill_contract_identity(
        invocation.skill_name or "",
        manifest=manifest,
    )
    if runtime_skill_identity != template.skill_contract_identity:
        raise RuntimeBindingError(
            "recipe_execution_contract_mismatch",
            "runtime skill contract differs from the compiled template",
        )
    contract_inputs = {input_def.name: input_def for input_def in contract.inputs}
    supplied = dict(skill_inputs or {})
    if skill_inputs is None:
        runtime_with_args: dict[str, object] = {"skill_command": skill_command}
        runtime_cwd = actual_mcp_kwargs.get("cwd")
        if isinstance(runtime_cwd, str):
            runtime_with_args["cwd"] = runtime_cwd
        runtime_inline = bind_step_invocation(
            step_name,
            RecipeStep(
                name=step_name,
                tool="run_skill",
                with_args=runtime_with_args,
                declared_with_args=dict(runtime_with_args),
            ),
            manifest=manifest,
        )
        if runtime_inline.failures:
            raise RuntimeBindingError(
                "recipe_execution_input_shape",
                runtime_inline.failures[0].message,
            )
        supplied = {
            value.name: value.effective_value
            for value in runtime_inline.skill_inputs
            if value.state is BoundValueState.PRESENT
            and isinstance(value.effective_value, (str, int, bool))
        }
    if any(
        not isinstance(value, (str, int, bool)) or value is None for value in supplied.values()
    ):
        raise RuntimeBindingError(
            "recipe_execution_input_type",
            "skill_inputs values must be strict JSON scalars",
        )
    expected_names = tuple(
        value.name for value in invocation.skill_inputs if value.state is BoundValueState.PRESENT
    )
    if frozenset(supplied) != frozenset(expected_names):
        raise RuntimeBindingError(
            "recipe_execution_input_shape",
            "skill_inputs keys do not exactly match the compiled template",
        )
    bound_inputs: list[tuple[str, BoundScalar]] = []
    for value in invocation.skill_inputs:
        if value.state is not BoundValueState.PRESENT:
            continue
        actual = supplied[value.name]
        input_def = contract_inputs.get(value.name)
        if input_def is None:
            raise RuntimeBindingError(
                "recipe_execution_contract_mismatch",
                f"compiled skill input {value.name!r} is absent from the runtime contract",
            )
        if not input_def.accepts(actual):
            raise RuntimeBindingError(
                "recipe_execution_input_type",
                f"runtime skill input {value.name!r} expects {input_def.type!r}",
            )
        if not _is_dynamic_binding(value) and actual != value.effective_value:
            raise RuntimeBindingError(
                "recipe_execution_static_input_mismatch",
                f"static skill input {value.name!r} differs from the template",
            )
        bound_inputs.append((value.name, actual))
    for value in invocation.mcp_kwargs:
        if value.name not in actual_mcp_kwargs:
            raise RuntimeBindingError(
                "recipe_execution_tool_shape",
                f"compiled tool parameter {value.name!r} is absent",
            )
        actual = actual_mcp_kwargs[value.name]
        if not _is_dynamic_binding(value) and actual != value.effective_value:
            raise RuntimeBindingError(
                "recipe_execution_static_tool_mismatch",
                f"static tool parameter {value.name!r} differs from the template",
            )
    return tuple(bound_inputs)
