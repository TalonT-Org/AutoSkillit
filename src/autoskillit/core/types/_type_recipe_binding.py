"""Frozen value objects for canonical recipe-step binding.

This module is IL-0 and stdlib-only.  Recipe compilation, semantic validation,
and server dispatch share these values without importing one another.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
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
    "FinalizedRecipeSegment",
    "FinalizedRecipeProjection",
    "RecipeBindingProjection",
    "RecipeFlowEdge",
    "ToolDef",
    "ToolInitializationOperation",
    "ToolParamDef",
    "ToolParamRole",
    "ToolWireType",
]


BoundScalar: TypeAlias = str | int | bool


class ToolWireType(StrEnum):
    """JSON-wire shapes accepted by an MCP parameter."""

    STRING = "string"
    INTEGER = "integer"
    NUMBER = "number"
    BOOLEAN = "boolean"
    SCALAR = "scalar"
    OBJECT = "object"
    ARRAY = "array"


class ToolInitializationOperation(StrEnum):
    """Operation class used by the recipe-initialization admission boundary."""

    RECOVERY = "recovery"
    INSPECTION = "inspection"
    LIFECYCLE_CONTROL = "lifecycle_control"
    EXECUTION = "execution"
    MUTATION = "mutation"


class ToolParamRole(StrEnum):
    """The single classification authority for what a tool parameter is for.

    The runtime attestation gate's always-admit set, the actual-kwargs
    assembly, and the server-side ``RecipeStep`` fallback obligation for
    execution-tuning parameters are all derived from this field — see
    ``runtime_exempt_param_names`` (core/tool_registry.py) and
    ``bind_runtime_skill_invocation`` (recipe/_binding.py).
    """

    PROTOCOL = "protocol"
    """Attestation identity (step_name, recipe_execution_id, invocation_template_digest);
    always admitted."""

    CHILD_INPUT = "child_input"
    """Child-skill inputs; admitted iff with:-compiled."""

    EXECUTION_TUNING = "execution_tuning"
    """Server-resolved from RecipeStep; forward only via with:."""

    ORCHESTRATOR_SCOPING = "orchestrator_scoping"
    """Runtime scoping; never recipe-authorable; always admitted."""

    SESSION_FLOW = "session_flow"
    """Resume/retry/capture plumbing; admitted iff with:-compiled."""


@dataclass(frozen=True, slots=True)
class ToolParamDef:
    """Static definition of one public MCP handler parameter."""

    name: str
    wire_type: ToolWireType = ToolWireType.SCALAR
    required: bool = False
    structured_skill_inputs: bool = False
    handler_parameter: bool = True
    role: ToolParamRole = field(kw_only=True)


@dataclass(frozen=True, slots=True)
class ToolDef:
    """Static definition of one registered MCP tool."""

    name: str
    params: tuple[ToolParamDef, ...]
    initialization_operation: ToolInitializationOperation
    # Successful result edges may carry the next recipe segment automatically.
    automatic_recipe_delivery: bool = False
    # Failure edges may carry a recovery segment; this can be enabled independently.
    recovery_recipe_delivery: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.initialization_operation, ToolInitializationOperation):
            raise TypeError(
                f"ToolDef {self.name!r} initialization_operation must be "
                "a ToolInitializationOperation"
            )
        params = tuple(self.params)
        for index, param in enumerate(params):
            if not isinstance(param, ToolParamDef):
                raise TypeError(f"ToolDef {self.name!r} parameter {index} must be a ToolParamDef")
        object.__setattr__(self, "params", params)

        names = tuple(param.name for param in params)
        if len(names) != len(set(names)):
            raise ValueError(f"ToolDef {self.name!r} contains duplicate parameters")
        structured = tuple(param.name for param in params if param.structured_skill_inputs)
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
        if not isinstance(self.state, BoundValueState):
            raise ValueError("BoundValue.state must be a BoundValueState")
        if not isinstance(self.origin, BoundValueOrigin):
            raise ValueError("BoundValue.origin must be a BoundValueOrigin")
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

    def __post_init__(self) -> None:
        mcp_kwargs = tuple(self.mcp_kwargs)
        skill_inputs = tuple(self.skill_inputs)
        failures = tuple(self.failures)
        if any(not isinstance(value, BoundValue) for value in mcp_kwargs):
            raise TypeError("mcp_kwargs must contain only BoundValue entries")
        if any(not isinstance(value, BoundValue) for value in skill_inputs):
            raise TypeError("skill_inputs must contain only BoundValue entries")
        if any(not isinstance(failure, BindingFailure) for failure in failures):
            raise TypeError("failures must contain only BindingFailure entries")
        object.__setattr__(self, "mcp_kwargs", mcp_kwargs)
        object.__setattr__(self, "skill_inputs", skill_inputs)
        object.__setattr__(self, "failures", failures)

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
            if not isinstance(effective, (str, int, bool)):
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


@dataclass(frozen=True, slots=True)
class RecipeFlowEdge:
    """One ordered route in a finalized recipe graph."""

    source: str
    edge_type: str
    target: str
    condition: str | None
    result_field: str | None

    def __post_init__(self) -> None:
        for field_name in ("source", "edge_type", "target"):
            value = getattr(self, field_name)
            if not isinstance(value, str) or not value:
                raise ValueError(f"RecipeFlowEdge.{field_name} must be a non-empty string")
        for field_name in ("condition", "result_field"):
            value = getattr(self, field_name)
            if value is not None and not isinstance(value, str):
                raise TypeError(f"RecipeFlowEdge.{field_name} must be a string or None")


@dataclass(frozen=True, slots=True)
class FinalizedRecipeSegment:
    """One finalized segment and the earlier steps that can deliver it."""

    name: str
    ordered_step_names: tuple[str, ...]
    checkpoint_sources: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.name, str) or not self.name.strip():
            raise ValueError("FinalizedRecipeSegment.name must be a non-empty string")
        ordered_step_names = tuple(self.ordered_step_names)
        if not ordered_step_names or any(
            not isinstance(step_name, str) or not step_name for step_name in ordered_step_names
        ):
            raise ValueError(
                "FinalizedRecipeSegment.ordered_step_names must contain non-empty strings"
            )
        if len(ordered_step_names) != len(set(ordered_step_names)):
            raise ValueError("FinalizedRecipeSegment.ordered_step_names contains duplicates")
        checkpoint_sources = tuple(self.checkpoint_sources)
        if any(
            not isinstance(step_name, str) or not step_name for step_name in checkpoint_sources
        ):
            raise ValueError(
                "FinalizedRecipeSegment.checkpoint_sources must contain non-empty strings"
            )
        if len(checkpoint_sources) != len(set(checkpoint_sources)):
            raise ValueError("FinalizedRecipeSegment.checkpoint_sources contains duplicates")
        object.__setattr__(self, "name", self.name.strip())
        object.__setattr__(self, "ordered_step_names", ordered_step_names)
        object.__setattr__(self, "checkpoint_sources", checkpoint_sources)


@dataclass(frozen=True, slots=True)
class FinalizedRecipeProjection:
    """Immutable execution projection of one fully finalized recipe."""

    binding_projection: RecipeBindingProjection
    ordered_step_names: tuple[str, ...]
    entrypoint: str
    ordered_flow_edges: tuple[RecipeFlowEdge, ...]
    delivery_segments: tuple[FinalizedRecipeSegment, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.binding_projection, RecipeBindingProjection):
            raise TypeError(
                "FinalizedRecipeProjection.binding_projection must be a RecipeBindingProjection"
            )

        ordered_step_names = tuple(self.ordered_step_names)
        if not ordered_step_names:
            raise ValueError("FinalizedRecipeProjection requires at least one ordered step")
        if any(not isinstance(name, str) or not name for name in ordered_step_names):
            raise ValueError(
                "FinalizedRecipeProjection.ordered_step_names must contain non-empty strings"
            )
        if len(ordered_step_names) != len(set(ordered_step_names)):
            raise ValueError("FinalizedRecipeProjection.ordered_step_names contains duplicates")
        if not isinstance(self.entrypoint, str) or not self.entrypoint:
            raise ValueError("FinalizedRecipeProjection.entrypoint must be a non-empty string")
        if self.entrypoint != ordered_step_names[0]:
            raise ValueError("FinalizedRecipeProjection.entrypoint must be the first ordered step")

        ordered_flow_edges = tuple(self.ordered_flow_edges)
        if any(not isinstance(edge, RecipeFlowEdge) for edge in ordered_flow_edges):
            raise TypeError(
                "FinalizedRecipeProjection.ordered_flow_edges must contain RecipeFlowEdge entries"
            )
        step_names = frozenset(ordered_step_names)
        if any(edge.source not in step_names for edge in ordered_flow_edges):
            raise ValueError(
                "FinalizedRecipeProjection flow-edge sources must be finalized step names"
            )

        delivery_segments = tuple(self.delivery_segments)
        if any(not isinstance(segment, FinalizedRecipeSegment) for segment in delivery_segments):
            raise TypeError(
                "FinalizedRecipeProjection.delivery_segments must contain "
                "FinalizedRecipeSegment entries"
            )
        if delivery_segments:
            segment_names = tuple(segment.name for segment in delivery_segments)
            if len(segment_names) != len(set(segment_names)):
                raise ValueError("FinalizedRecipeProjection delivery segment names must be unique")
            flattened_steps = tuple(
                step_name
                for segment in delivery_segments
                for step_name in segment.ordered_step_names
            )
            if flattened_steps != ordered_step_names:
                raise ValueError(
                    "FinalizedRecipeProjection delivery segments must partition ordered steps"
                )
            segment_index = {
                step_name: index
                for index, segment in enumerate(delivery_segments)
                for step_name in segment.ordered_step_names
            }
            for target_index, segment in enumerate(delivery_segments):
                if any(source not in step_names for source in segment.checkpoint_sources):
                    raise ValueError(
                        "FinalizedRecipeProjection checkpoint sources must be finalized steps"
                    )
                if any(
                    segment_index[source] >= target_index for source in segment.checkpoint_sources
                ):
                    raise ValueError(
                        "FinalizedRecipeProjection checkpoint sources must precede their target"
                    )

        object.__setattr__(self, "ordered_step_names", ordered_step_names)
        object.__setattr__(self, "ordered_flow_edges", ordered_flow_edges)
        object.__setattr__(self, "delivery_segments", delivery_segments)
