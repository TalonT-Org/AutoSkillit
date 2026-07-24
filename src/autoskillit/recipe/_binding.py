"""Pure compiler for recipe tool and child-skill invocations."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Final, TypeGuard

import regex as re

from autoskillit.core import (
    BindingFailure,
    BindingFailureCode,
    BindingMode,
    BoundScalar,
    BoundStepInvocation,
    BoundValue,
    BoundValueOrigin,
    BoundValueState,
    RecipeBindingProjection,
    ToolParamDef,
    ToolWireType,
    get_tool_def,
    resolve_skill_name,
)
from autoskillit.recipe._contracts_manifest import (
    get_callable_contract,
    get_skill_contract,
    load_bundled_manifest,
)
from autoskillit.recipe._contracts_types import SkillContract, SkillInput

__all__ = [
    "bind_recipe",
    "bind_step_invocation",
]


_CONTEXT_REF_RE: Final = re.compile(r"\$\{\{\s*context\.([A-Za-z_]\w*)\s*\}\}")
_INPUT_REF_RE: Final = re.compile(r"\$\{\{\s*inputs\.([A-Za-z_]\w*)\s*\}\}")
_AUTOSKILLIT_TEMPLATE_RE: Final = re.compile(r"\{\{(AUTOSKILLIT_[A-Z0-9_]+)\}\}")
_EXACT_CONTEXT_REF_RE: Final = re.compile(r"^\$\{\{\s*context\.([A-Za-z_]\w*)\s*\}\}$")
_EXACT_INPUT_REF_RE: Final = re.compile(r"^\$\{\{\s*inputs\.([A-Za-z_]\w*)\s*\}\}$")
_SCALAR_TYPES = (str, int, float, bool)


def _is_scalar(value: object) -> TypeGuard[BoundScalar]:
    return isinstance(value, _SCALAR_TYPES) and value is not None


def _origin_for(
    declared: BoundScalar,
) -> tuple[
    BoundValueOrigin,
    tuple[str, ...],
    tuple[str, ...],
    tuple[str, ...],
]:
    if not isinstance(declared, str):
        return BoundValueOrigin.LITERAL, (), (), ()
    context_dependencies = tuple(dict.fromkeys(_CONTEXT_REF_RE.findall(declared)))
    input_dependencies = tuple(dict.fromkeys(_INPUT_REF_RE.findall(declared)))
    template_dependencies = tuple(dict.fromkeys(_AUTOSKILLIT_TEMPLATE_RE.findall(declared)))
    if _EXACT_CONTEXT_REF_RE.fullmatch(declared):
        origin = BoundValueOrigin.CONTEXT
    elif _EXACT_INPUT_REF_RE.fullmatch(declared):
        origin = BoundValueOrigin.RECIPE_INPUT
    elif (
        context_dependencies
        or input_dependencies
        or "${{" in declared
        or "{{AUTOSKILLIT_" in declared
    ):
        origin = BoundValueOrigin.TEMPLATE
    else:
        origin = BoundValueOrigin.LITERAL
    return origin, context_dependencies, input_dependencies, template_dependencies


def _bound_value(name: str, declared: BoundScalar, effective: BoundScalar) -> BoundValue:
    (
        origin,
        context_dependencies,
        input_dependencies,
        template_dependencies,
    ) = _origin_for(declared)
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


def _failure(
    code: BindingFailureCode,
    step_name: str,
    name: str,
    message: str,
) -> BindingFailure:
    return BindingFailure(code=code, step_name=step_name, name=name, message=message)


def _wire_value_is_valid(value: object, param: ToolParamDef) -> bool:
    match param.wire_type:
        case ToolWireType.STRING:
            return isinstance(value, str)
        case ToolWireType.INTEGER:
            return isinstance(value, int) and not isinstance(value, bool)
        case ToolWireType.NUMBER:
            return isinstance(value, (int, float)) and not isinstance(value, bool)
        case ToolWireType.BOOLEAN:
            return isinstance(value, bool)
        case ToolWireType.SCALAR:
            return _is_scalar(value)
        case ToolWireType.OBJECT:
            return isinstance(value, Mapping)
        case ToolWireType.ARRAY:
            return isinstance(value, (list, tuple))


def _skill_value_is_valid(value: BoundScalar, input_def: SkillInput) -> bool:
    normalized = input_def.type
    if normalized in {
        "str",
        "string",
        "optional_string",
        "file_path",
        "file_path_list",
        "directory_path",
    }:
        return isinstance(value, str)
    if normalized == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if normalized in {"number", "float"}:
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if normalized in {"boolean", "bool"}:
        return isinstance(value, bool)
    return False


def _resolve_hidden_value(
    declared: BoundScalar,
    effective: BoundScalar,
    *,
    hidden_inputs: frozenset[str],
    ingredient_values: Mapping[str, BoundScalar],
) -> BoundScalar:
    """Resolve hidden inputs while retaining declaration-derived provenance."""

    if not isinstance(declared, str):
        return effective
    exact = _EXACT_INPUT_REF_RE.fullmatch(declared)
    if exact and exact.group(1) in hidden_inputs:
        return ingredient_values.get(exact.group(1), effective)
    if not isinstance(effective, str):
        return effective

    def replace(match: re.Match[str]) -> str:
        name = match.group(1)
        if name not in hidden_inputs or name not in ingredient_values:
            return match.group(0)
        return str(ingredient_values[name])

    return _INPUT_REF_RE.sub(replace, effective)


def _tokenize_skill_command(command: str) -> tuple[str, ...]:
    """Tokenize without evaluating shell syntax and keep template refs atomic."""

    tokens: list[str] = []
    current: list[str] = []
    quote: str | None = None
    index = 0
    while index < len(command):
        char = command[index]
        if quote is not None:
            if char == "\\" and index + 1 < len(command):
                current.extend((char, command[index + 1]))
                index += 2
                continue
            current.append(char)
            if char == quote:
                quote = None
            index += 1
            continue
        if char in {"'", '"'}:
            quote = char
            current.append(char)
            index += 1
            continue
        if command.startswith("${{", index):
            end = command.find("}}", index + 3)
            if end < 0:
                current.append(command[index:])
                index = len(command)
            else:
                current.append(command[index : end + 2])
                index = end + 2
            continue
        if char.isspace():
            if current:
                tokens.append("".join(current))
                current = []
            index += 1
            continue
        current.append(char)
        index += 1
    if quote is not None:
        raise ValueError("unterminated quoted skill argument")
    if current:
        tokens.append("".join(current))
    return tuple(tokens)


def _unquote(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        return value[1:-1]
    return value


def _split_named_token(token: str) -> tuple[str, str] | None:
    if "=" not in token:
        return None
    name, value = token.split("=", 1)
    if not re.fullmatch(r"[A-Za-z_]\w*", name):
        return None
    return name, _unquote(value)


def _inline_skill_inputs(
    *,
    step_name: str,
    declared_command: str,
    effective_command: str,
    contract: SkillContract,
) -> tuple[tuple[BoundValue, ...], tuple[BindingFailure, ...]]:
    try:
        declared_tokens = _tokenize_skill_command(declared_command)
        effective_tokens = _tokenize_skill_command(effective_command)
    except ValueError as exc:
        return (), (
            _failure(
                BindingFailureCode.INVALID_SKILL_COMMAND,
                step_name,
                "skill_command",
                str(exc),
            ),
        )
    declared_args = declared_tokens[1:]
    effective_args = effective_tokens[1:]
    if len(declared_args) != len(effective_args):
        return (), (
            _failure(
                BindingFailureCode.INVALID_SKILL_COMMAND,
                step_name,
                "skill_command",
                "declared and effective skill arguments do not align",
            ),
        )

    input_defs = contract.inputs
    input_by_name = {input_def.name: input_def for input_def in input_defs}
    if (
        len(input_defs) == 1
        and declared_args
        and all(_split_named_token(token) is None for token in declared_args)
    ):
        # Slash-command callers conventionally pass a free-form prose tail for a
        # single input. Preserve that complete tail as one value instead of
        # treating each word as a separate positional input.
        declared_args = (" ".join(declared_args),)
        effective_args = (" ".join(effective_args),)
    assigned: dict[str, tuple[BoundScalar, BoundScalar]] = {}
    failures: list[BindingFailure] = []
    position = 0
    for declared_token, effective_token in zip(declared_args, effective_args, strict=True):
        declared_named = _split_named_token(declared_token)
        effective_named = _split_named_token(effective_token)
        if declared_named is not None:
            name, declared_value = declared_named
            if effective_named is None or effective_named[0] != name:
                failures.append(
                    _failure(
                        BindingFailureCode.INVALID_SKILL_COMMAND,
                        step_name,
                        name,
                        "declared and effective named arguments do not align",
                    )
                )
                continue
            effective_value = effective_named[1]
            if name not in input_by_name:
                failures.append(
                    _failure(
                        BindingFailureCode.UNKNOWN_SKILL_INPUT,
                        step_name,
                        name,
                        f"skill input {name!r} is not declared by the selected contract",
                    )
                )
                continue
        else:
            while position < len(input_defs) and input_defs[position].name in assigned:
                position += 1
            if position >= len(input_defs):
                failures.append(
                    _failure(
                        BindingFailureCode.DEAD_SKILL_INPUT,
                        step_name,
                        f"arg{position}",
                        "skill command contains more positional values than the contract",
                    )
                )
                position += 1
                continue
            name = input_defs[position].name
            declared_value = _unquote(declared_token)
            effective_value = _unquote(effective_token)
            position += 1
        if name in assigned:
            failures.append(
                _failure(
                    BindingFailureCode.AMBIGUOUS_SKILL_INPUT,
                    step_name,
                    name,
                    f"skill input {name!r} is supplied more than once",
                )
            )
            continue
        if declared_value == "-":
            continue
        assigned[name] = (declared_value, effective_value)

    bound: list[BoundValue] = []
    for input_def in input_defs:
        pair = assigned.get(input_def.name)
        if pair is None:
            bound.append(BoundValue.absent(input_def.name))
            if input_def.required:
                failures.append(
                    _failure(
                        BindingFailureCode.MISSING_SKILL_INPUT,
                        step_name,
                        input_def.name,
                        f"required skill input {input_def.name!r} is absent",
                    )
                )
            continue
        bound_declared, bound_effective = pair
        value = _bound_value(input_def.name, bound_declared, bound_effective)
        bound.append(value)
        unresolved = bool(value.context_dependencies or value.input_dependencies)
        if not unresolved and not _skill_value_is_valid(bound_effective, input_def):
            failures.append(
                _failure(
                    BindingFailureCode.INVALID_SKILL_INPUT_TYPE,
                    step_name,
                    input_def.name,
                    f"skill input {input_def.name!r} expects {input_def.type!r}",
                )
            )
    return tuple(bound), tuple(failures)


def _structured_skill_inputs(
    *,
    step_name: str,
    declared_values: Mapping[str, object],
    effective_values: Mapping[str, object],
    contract: SkillContract,
    hidden_inputs: frozenset[str],
    ingredient_values: Mapping[str, BoundScalar],
) -> tuple[tuple[BoundValue, ...], tuple[BindingFailure, ...]]:
    input_by_name = {input_def.name: input_def for input_def in contract.inputs}
    failures: list[BindingFailure] = []
    for name in declared_values:
        if name not in input_by_name:
            failures.append(
                _failure(
                    BindingFailureCode.UNKNOWN_SKILL_INPUT,
                    step_name,
                    name,
                    f"skill input {name!r} is not declared by the selected contract",
                )
            )

    bound: list[BoundValue] = []
    for input_def in contract.inputs:
        if input_def.name not in declared_values:
            bound.append(BoundValue.absent(input_def.name))
            if input_def.required:
                failures.append(
                    _failure(
                        BindingFailureCode.MISSING_SKILL_INPUT,
                        step_name,
                        input_def.name,
                        f"required skill input {input_def.name!r} is absent",
                    )
                )
            continue
        declared = declared_values[input_def.name]
        effective = effective_values.get(input_def.name, declared)
        if not _is_scalar(declared) or not _is_scalar(effective):
            failures.append(
                _failure(
                    BindingFailureCode.INVALID_SKILL_INPUT_TYPE,
                    step_name,
                    input_def.name,
                    f"skill input {input_def.name!r} must be a strict scalar",
                )
            )
            bound.append(BoundValue.absent(input_def.name))
            continue
        resolved = _resolve_hidden_value(
            declared,
            effective,
            hidden_inputs=hidden_inputs,
            ingredient_values=ingredient_values,
        )
        value = _bound_value(input_def.name, declared, resolved)
        bound.append(value)
        unresolved = bool(value.context_dependencies or value.input_dependencies)
        if not unresolved and not _skill_value_is_valid(resolved, input_def):
            failures.append(
                _failure(
                    BindingFailureCode.INVALID_SKILL_INPUT_TYPE,
                    step_name,
                    input_def.name,
                    f"skill input {input_def.name!r} expects {input_def.type!r}",
                )
            )
    return tuple(bound), tuple(failures)


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
    declared_with: Mapping[str, object] = (
        getattr(step, "declared_with_args", None) or effective_with
    )
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
    active_manifest = manifest or load_bundled_manifest()
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
            mcp_kwargs.append(_bound_structured_value(param.name, declared, effective))
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
    active_manifest = manifest or load_bundled_manifest()
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
