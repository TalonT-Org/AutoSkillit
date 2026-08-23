"""Context-admission scalar identities and lineage values."""

from __future__ import annotations

from dataclasses import dataclass, field

from ._type_context_admission_base import _ContractValue
from ._type_dispatch_identity import DispatchIdentity
from ._type_enums import ProducerSurface
from ._type_helpers import _raise_invalid, _validate_bounded_text, _validate_non_negative

_NON_DISPATCH_PRODUCER_SURFACES = frozenset(
    {
        ProducerSurface.TOOL_ARGUMENT,
        ProducerSurface.TOOL_RESULT_ENVELOPE,
        ProducerSurface.USER_PROMPT,
        ProducerSurface.ASSISTANT_OUTPUT_HISTORY,
        ProducerSurface.SKILL_PLUGIN_CONTEXT,
        ProducerSurface.OTHER_CONTEXT_INJECTION,
        ProducerSurface.CLIENT_PROVIDER_RETRIEVAL,
        ProducerSurface.CODE_MODE_AGGREGATE,
        ProducerSurface.HOSTED_SPECIALIZED_TOOL,
        ProducerSurface.HOOK_FEEDBACK,
        ProducerSurface.COMPACTION_MODEL_WINDOW_TRANSITION,
    }
)


@dataclass(frozen=True, slots=True)
class _OpaqueString(_ContractValue):
    value: str

    def __post_init__(self) -> None:
        _validate_bounded_text(
            self.value,
            "invalid_opaque_identifier",
            maximum=96,
        )
        allowed = "-_.:"
        if (
            len(self.value) in {40, 64, 128}
            and all(character in "0123456789abcdefABCDEF" for character in self.value)
        ) or (
            self.value.startswith("-")
            or self.value.endswith("-")
            or any(
                not (character.isascii() and (character.isalnum() or character in allowed))
                for character in self.value
            )
        ):
            _raise_invalid("invalid_opaque_identifier")


@dataclass(frozen=True, slots=True)
class _NonNegativeInteger(_ContractValue):
    value: int

    def __post_init__(self) -> None:
        _validate_non_negative(self.value, "invalid_non_negative_integer")


@dataclass(frozen=True, slots=True)
class ContextSessionId(_OpaqueString):
    pass


@dataclass(frozen=True, slots=True)
class AgentInstanceId(_OpaqueString):
    pass


@dataclass(frozen=True, slots=True)
class ContextThreadId(_OpaqueString):
    pass


@dataclass(frozen=True, slots=True)
class ForkOccurrenceId(_OpaqueString):
    pass


@dataclass(frozen=True, slots=True)
class TurnId(_OpaqueString):
    pass


@dataclass(frozen=True, slots=True)
class ProducerInstanceId(_OpaqueString):
    pass


@dataclass(frozen=True, slots=True)
class ToolCallId(_OpaqueString):
    pass


@dataclass(frozen=True, slots=True)
class ModelItemId(_OpaqueString):
    pass


@dataclass(frozen=True, slots=True)
class AdmissionRequestId(_OpaqueString):
    pass


@dataclass(frozen=True, slots=True)
class AdmissionBatchId(_OpaqueString):
    pass


@dataclass(frozen=True, slots=True)
class WindowEpochId(_OpaqueString):
    pass


@dataclass(frozen=True, slots=True)
class TokenizerIdentity(_OpaqueString):
    pass


@dataclass(frozen=True, slots=True)
class CanonicalSpanId(_OpaqueString):
    pass


@dataclass(frozen=True, slots=True)
class AdmissionOccurrenceId(_OpaqueString):
    pass


@dataclass(frozen=True, slots=True)
class AdmissionAttemptId(_OpaqueString):
    pass


@dataclass(frozen=True, slots=True)
class DeliveryOccurrenceId(_OpaqueString):
    pass


@dataclass(frozen=True, slots=True)
class AdmissionEventId(_OpaqueString):
    pass


@dataclass(frozen=True, slots=True)
class AdmissionReservationId(_OpaqueString):
    pass


@dataclass(frozen=True, slots=True)
class AdmissionWitnessId(_OpaqueString):
    pass


@dataclass(frozen=True, slots=True)
class AuthoritySourceId(_OpaqueString):
    pass


@dataclass(frozen=True, slots=True)
class GenerationReservationId(_OpaqueString):
    pass


@dataclass(frozen=True, slots=True)
class ProtectedPoolOwnerId(_OpaqueString):
    pass


@dataclass(frozen=True, slots=True)
class RepresentationRevision(_OpaqueString):
    pass


@dataclass(frozen=True, slots=True)
class RepresentationBindingId(_OpaqueString):
    pass


@dataclass(frozen=True, slots=True)
class AggregateRevision(_NonNegativeInteger):
    pass


@dataclass(frozen=True, slots=True)
class AdmissionSequence(_NonNegativeInteger):
    pass


@dataclass(frozen=True, slots=True)
class IdempotencyNamespace(_ContractValue):
    caller_scope: str
    operation_kind: str

    def __post_init__(self) -> None:
        _validate_bounded_text(self.caller_scope, "invalid_idempotency_namespace")
        _validate_bounded_text(self.operation_kind, "invalid_idempotency_operation")


@dataclass(frozen=True, slots=True)
class ContextLineage(_ContractValue):
    root_session_id: ContextSessionId
    current_session_id: ContextSessionId
    root_agent_id: AgentInstanceId
    current_agent_id: AgentInstanceId
    parent_agent_id: AgentInstanceId | None
    root_thread_id: ContextThreadId
    current_thread_id: ContextThreadId
    parent_thread_id: ContextThreadId | None
    fork_occurrence_id: ForkOccurrenceId | None
    turn_id: TurnId
    producer_surface: ProducerSurface
    producer_instance_id: ProducerInstanceId
    tool_call_id: ToolCallId | None
    model_item_id: ModelItemId | None
    dispatch_identity: DispatchIdentity | None = field(repr=False)
    attempt_id: AdmissionAttemptId
    delivery_occurrence_id: DeliveryOccurrenceId | None
    window_epoch_id: WindowEpochId
    window_epoch_number: int

    def __post_init__(self) -> None:
        _validate_non_negative(self.window_epoch_number, "invalid_window_epoch_number")
        if self.dispatch_identity is not None:
            expected = DispatchIdentity.from_dispatch_id(self.dispatch_identity.dispatch_id)
            if self.dispatch_identity != expected:
                _raise_invalid("invalid_dispatch_identity")
            if self.producer_surface in _NON_DISPATCH_PRODUCER_SURFACES:
                _raise_invalid("dispatch_identity_on_non_dispatch_surface")
        is_parent_delivery = self.producer_surface is ProducerSurface.PARENT_VISIBLE_CHILD_DELIVERY
        if is_parent_delivery != (self.delivery_occurrence_id is not None):
            _raise_invalid("invalid_parent_delivery_lineage")
        if is_parent_delivery and (
            self.parent_agent_id is None
            or self.parent_thread_id is None
            or self.fork_occurrence_id is None
        ):
            _raise_invalid("incomplete_parent_delivery_lineage")
