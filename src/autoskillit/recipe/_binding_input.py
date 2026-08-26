"""Recipe skill-input parsing (#4854).

Extracted from ``recipe/_binding.py`` so the input-parsing cluster
(tokenization + inline + structured) is separable from compile-time binding
(``bind_step_invocation``, ``bind_recipe``) and runtime attestation admission
(``bind_runtime_skill_invocation``).

Public functions consumed by ``_binding.py``:
    - ``_tokenize_skill_command`` — inline-only detection in ``bind_step_invocation``
    - ``_inline_skill_inputs`` — inline command parsing in ``bind_step_invocation``
    - ``_structured_skill_inputs`` — structured mapping parsing in ``bind_step_invocation``
    - ``_failure`` — record binding failures in ``bind_step_invocation``
    - ``_is_scalar`` — used by ``_wire_value_is_valid``, ``bind_step_invocation``, ``bind_recipe``
    - ``_bound_value`` — tool-param binding in ``bind_step_invocation``
    - ``_resolve_hidden_value`` — hidden-ingredient substitution in ``bind_step_invocation``
    - ``_origin_for`` (transitive via ``_bound_value``)
    - Regex constants — used by ``_structured_dependencies`` (stays in ``_binding.py``)

All helpers in this module are private (``_``-prefixed). Public API stays in ``_binding.py``.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace
from typing import Final, TypeGuard

import regex as re

from autoskillit.core import (
    BindingFailure,
    BindingFailureCode,
    BoundScalar,
    BoundValue,
    BoundValueOrigin,
    BoundValueState,
)
from autoskillit.recipe._contracts_types import SkillContract, SkillInput

# === Module-level regex constants (moved from _binding.py lines 49-53) ===

_CONTEXT_REF_RE: Final = re.compile(r"\$\{\{\s*context\.([A-Za-z_]\w*)\s*\}\}")
_INPUT_REF_RE: Final = re.compile(r"\$\{\{\s*inputs\.([A-Za-z_]\w*)\s*\}\}")
_AUTOSKILLIT_TEMPLATE_RE: Final = re.compile(r"\{\{(AUTOSKILLIT_[A-Z0-9_]+)\}\}")
_EXACT_CONTEXT_REF_RE: Final = re.compile(r"^\$\{\{\s*context\.([A-Za-z_]\w*)\s*\}\}$")
_EXACT_INPUT_REF_RE: Final = re.compile(r"^\$\{\{\s*inputs\.([A-Za-z_]\w*)\s*\}\}$")

# === Scalar tuple constant (moved from _binding.py line 54) ===

_SCALAR_TYPES = (str, int, bool)


# === Provenance helpers (moved from _binding.py lines 79-80, 83-109, 112-128) ===


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


# === Failure-recording helper (moved from _binding.py lines 194-200) ===


def _failure(
    code: BindingFailureCode,
    step_name: str,
    name: str,
    message: str,
) -> BindingFailure:
    return BindingFailure(code=code, step_name=step_name, name=name, message=message)


# === Skill-input type validation (moved from _binding.py lines 221-222) ===


def _skill_value_is_valid(value: BoundScalar, input_def: SkillInput) -> bool:
    return input_def.accepts(value)


# === Hidden-ingredient substitution (moved from _binding.py lines 225-248) ===


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


# === Command tokenization (moved from _binding.py lines 251-311) ===


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


# === Skill-input parsers (moved from _binding.py lines 314-534) ===


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
    optional_context_refs: frozenset[str],
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
        exact_context = (
            _EXACT_CONTEXT_REF_RE.fullmatch(declared) if isinstance(declared, str) else None
        )
        if (
            exact_context is not None
            and exact_context.group(1) in optional_context_refs
            and effective == declared
            and input_def.has_absence_value
        ):
            absence_value = input_def.absence_value
            assert isinstance(absence_value, (str, int, bool))
            resolved = absence_value
        value = _bound_value(input_def.name, declared, resolved)
        if (
            not input_def.required
            and frozenset(value.context_dependencies) & optional_context_refs
        ):
            value = replace(value, absence_value=input_def.absence_value)
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
