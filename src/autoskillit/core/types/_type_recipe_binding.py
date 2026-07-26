"""Frozen value objects for canonical recipe-step binding.

This module is IL-0 and stdlib-only.  Recipe compilation, semantic validation,
and server dispatch share these values without importing one another.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from types import MappingProxyType
from typing import TypeAlias

__all__ = [
    "ABSENT_BOUND_VALUE",
    "AbsentBoundValue",
    "BindingFailure",
    "BindingFailureCode",
    "BindingMode",
    "BoundScalar",
    "BoundStepInvocation",
    "BoundValue",
    "BoundValueOrigin",
    "BoundValueState",
    "RecipeBindingProjection",
    "ToolDef",
    "ToolParamDef",
    "ToolWireType",
]


BoundScalar: TypeAlias = str | int | float | bool


class ToolWireType(StrEnum):
    """JSON-wire shapes accepted by an MCP parameter."""

    STRING = "string"
    INTEGER = "integer"
    NUMBER = "number"
    BOOLEAN = "boolean"
    SCALAR = "scalar"
    OBJECT = "object"
    ARRAY = "array"


@dataclass(frozen=True, slots=True)
class ToolParamDef:
    """Static definition of one public MCP handler parameter."""

    name: str
    wire_type: ToolWireType = ToolWireType.SCALAR
    required: bool = False
    structured_skill_inputs: bool = False
    handler_parameter: bool = True


@dataclass(frozen=True, slots=True)
class ToolDef:
    """Static definition of one registered MCP tool."""

    name: str
    params: tuple[ToolParamDef, ...]

    def __post_init__(self) -> None:
        names = tuple(param.name for param in self.params)
        if len(names) != len(set(names)):
            raise ValueError(f"ToolDef {self.name!r} contains duplicate parameters")
        structured = tuple(param.name for param in self.params if param.structured_skill_inputs)
        if structured not in {(), ("skill_inputs",)}:
            raise ValueError(
                f"ToolDef {self.name!r} has invalid structured parameters: {structured!r}"
            )
        if structured and self.name != "run_skill":
            raise ValueError("Only run_skill may declare structured skill_inputs")

    @property
    def param_names(self) -> tuple[str, ...]:
        return tuple(param.name for param in self.params)

    @property
    def param_set(self) -> frozenset[str]:
        return frozenset(self.param_names)

    @property
    def handler_param_set(self) -> frozenset[str]:
        return frozenset(param.name for param in self.params if param.handler_parameter)

    def param_def(self, name: str) -> ToolParamDef | None:
        return next((param for param in self.params if param.name == name), None)


class BindingMode(StrEnum):
    """Binding trust boundary."""

    RECIPE = "recipe"
    STANDALONE = "standalone"


class BoundValueState(StrEnum):
    PRESENT = "present"
    ABSENT = "absent"


class BoundValueOrigin(StrEnum):
    """Semantic origin determined from the declaration, never the replacement."""

    LITERAL = "literal"
    RECIPE_INPUT = "recipe_input"
    CONTEXT = "context"
    TEMPLATE = "template"
    ABSENT = "absent"


class AbsentBoundValue(StrEnum):
    """Dedicated optional-absence sentinel.

    It is intentionally not ``None`` or a falsey scalar, so ``""``, ``False``,
    and ``0`` remain contract-valid present values.
    """

    TOKEN = "absent"


ABSENT_BOUND_VALUE = AbsentBoundValue.TOKEN


@dataclass(frozen=True, slots=True)
class BoundValue:
    """One aligned declared/effective value in a named binding slot."""

    name: str
    declared_value: object | AbsentBoundValue
    effective_value: object | AbsentBoundValue
    state: BoundValueState
    origin: BoundValueOrigin
    context_dependencies: tuple[str, ...] = ()
    input_dependencies: tuple[str, ...] = ()
    template_dependencies: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        declared_absent = isinstance(self.declared_value, AbsentBoundValue)
        effective_absent = isinstance(self.effective_value, AbsentBoundValue)
        if self.state is BoundValueState.ABSENT:
            if (
                not declared_absent
                or not effective_absent
                or self.origin is not BoundValueOrigin.ABSENT
            ):
                raise ValueError(
                    "absent bound values require absent declared/effective values and origin"
                )
        elif declared_absent or effective_absent or self.origin is BoundValueOrigin.ABSENT:
            raise ValueError("present bound values cannot use an absent value or origin")

    @classmethod
    def absent(cls, name: str) -> BoundValue:
        return cls(
            name=name,
            declared_value=ABSENT_BOUND_VALUE,
            effective_value=ABSENT_BOUND_VALUE,
            state=BoundValueState.ABSENT,
            origin=BoundValueOrigin.ABSENT,
        )

    @property
    def is_present(self) -> bool:
        return self.state is BoundValueState.PRESENT


class BindingFailureCode(StrEnum):
    UNKNOWN_TOOL = "unknown_tool"
    UNKNOWN_TOOL_PARAMETER = "unknown_tool_parameter"
    MISSING_TOOL_PARAMETER = "missing_tool_parameter"
    INVALID_TOOL_PARAMETER_TYPE = "invalid_tool_parameter_type"
    UNKNOWN_SKILL = "unknown_skill"
    UNKNOWN_SKILL_INPUT = "unknown_skill_input"
    MISSING_SKILL_INPUT = "missing_skill_input"
    DEAD_SKILL_INPUT = "dead_skill_input"
    AMBIGUOUS_SKILL_INPUT = "ambiguous_skill_input"
    INVALID_SKILL_INPUT_TYPE = "invalid_skill_input_type"
    INVALID_SKILL_COMMAND = "invalid_skill_command"


@dataclass(frozen=True, slots=True)
class BindingFailure:
    code: BindingFailureCode
    step_name: str
    name: str
    message: str


@dataclass(frozen=True, slots=True)
class BoundStepInvocation:
    """Canonical compile result for one recipe step."""

    step_name: str
    tool_name: str
    mode: BindingMode
    skill_name: str | None
    mcp_kwargs: tuple[BoundValue, ...]
    skill_inputs: tuple[BoundValue, ...]
    failures: tuple[BindingFailure, ...] = ()

    @property
    def is_valid(self) -> bool:
        return not self.failures

    @property
    def attested(self) -> bool:
        """Whether this binding is eligible for later recipe attestation."""

        return self.mode is BindingMode.RECIPE and self.is_valid and self.skill_name is not None

    @property
    def canonical_child_invocation(self) -> tuple[tuple[str, BoundScalar], ...]:
        """Ordered, named child inputs with absent optionals omitted.

        This is data, not a shell command.  No quoting, interpolation, or
        positional compaction occurs at this boundary.
        """

        result: list[tuple[str, BoundScalar]] = []
        for value in self.skill_inputs:
            if not value.is_present:
                continue
            effective = value.effective_value
            if isinstance(effective, AbsentBoundValue):
                continue
            if not isinstance(effective, (str, int, float, bool)):
                raise TypeError(f"child-skill input {value.name!r} is not a strict scalar")
            result.append((value.name, effective))
        return tuple(result)

    def skill_input(self, name: str) -> BoundValue | None:
        return next((value for value in self.skill_inputs if value.name == name), None)


@dataclass(frozen=True, slots=True)
class RecipeBindingProjection:
    """Immutable step-name projection for one validation boundary."""

    invocations: Mapping[str, BoundStepInvocation]

    def __post_init__(self) -> None:
        object.__setattr__(self, "invocations", MappingProxyType(dict(self.invocations)))

    def for_step(self, step_name: str) -> BoundStepInvocation | None:
        return self.invocations.get(step_name)

    @property
    def failures(self) -> tuple[BindingFailure, ...]:
        return tuple(
            failure for invocation in self.invocations.values() for failure in invocation.failures
        )
