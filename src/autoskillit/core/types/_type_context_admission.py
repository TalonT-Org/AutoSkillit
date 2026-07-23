"""Pure protocol-v1 values for cumulative context admission.

The contract is intentionally content-free and implementation-independent.  It
contains immutable commands, records, decisions, and declarative publication
effects; persistence and producer integration belong to downstream layers.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, ClassVar, Never, TypeAlias

from ._type_dispatch_identity import DispatchIdentity
from ._type_enums import (
    AdmissionDecisionKind,
    AdmissionState,
    ChargeDomain,
    CoverageEvidenceKind,
    CoverageState,
    GenerationState,
    MeasurementKind,
    ProducerSurface,
    ReserveClass,
    WitnessKind,
)
from ._type_results import ModelIdentity

CONTEXT_ADMISSION_PROTOCOL_VERSION = 1


class ContextAdmissionValidationError(ValueError):
    """Raised when a protocol value violates a content-free invariant."""


class UnsupportedContextAdmissionProtocolError(ContextAdmissionValidationError):
    """Raised when a value uses unsupported protocol semantics."""


_TYPE_REGISTRY: dict[str, type[_ContractValue]] = {}
_ENUM_REGISTRY: dict[str, type[StrEnum]] = {
    enum_type.__name__: enum_type
    for enum_type in (
        AdmissionDecisionKind,
        AdmissionState,
        ChargeDomain,
        CoverageEvidenceKind,
        CoverageState,
        GenerationState,
        MeasurementKind,
        ProducerSurface,
        ReserveClass,
        WitnessKind,
    )
}


def _raise_invalid(reason_code: str) -> Never:
    raise ContextAdmissionValidationError(reason_code)


def _validate_protocol_version(protocol_version: int) -> None:
    if protocol_version != CONTEXT_ADMISSION_PROTOCOL_VERSION:
        raise UnsupportedContextAdmissionProtocolError("unsupported_protocol_version")


def _validate_non_negative(value: int, reason_code: str) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        _raise_invalid(reason_code)


def _validate_bounded_text(
    value: str,
    reason_code: str,
    *,
    maximum: int = 128,
) -> None:
    if not isinstance(value, str) or not value or len(value) > maximum:
        _raise_invalid(reason_code)
    lowered = value.casefold()
    if (
        lowered.startswith("bearer")
        or lowered.startswith("sha256:")
        or lowered.startswith("blake2:")
        or lowered.startswith("content:")
        or "\n" in value
        or "\r" in value
        or value.startswith("/")
        or "\\" in value
        or value.startswith("~")
    ):
        _raise_invalid(reason_code)


def _validate_tuple(value: object, reason_code: str) -> None:
    if not isinstance(value, tuple):
        _raise_invalid(reason_code)


def _encode(value: object) -> object:
    if isinstance(value, DispatchIdentity):
        return {"dispatch_id": value.dispatch_id}
    if isinstance(value, ModelIdentity):
        return {
            "__type__": "ModelIdentity",
            "configured_model": value.configured_model,
            "effective_model": value.effective_model,
            "profile_name": value.profile_name,
        }
    if isinstance(value, StrEnum):
        return {"__enum__": type(value).__name__, "value": value.value}
    if isinstance(value, tuple):
        return {"__tuple__": [_encode(item) for item in value]}
    if isinstance(value, frozenset):
        encoded = [_encode(item) for item in value]
        encoded.sort(key=repr)
        return {"__frozenset__": encoded}
    if isinstance(value, _ContractValue):
        result: dict[str, object] = {"__type__": type(value).__name__}
        for field_name in value.__dataclass_fields__:
            result[field_name] = _encode(getattr(value, field_name))
        return result
    if value is None or isinstance(value, bool | int | str):
        return value
    _raise_invalid("unsupported_serialization_value")


def _decode(value: object) -> object:
    if isinstance(value, list):
        return tuple(_decode(item) for item in value)
    if not isinstance(value, Mapping):
        return value
    if set(value) == {"dispatch_id"}:
        dispatch_id = value["dispatch_id"]
        if not isinstance(dispatch_id, str):
            _raise_invalid("invalid_dispatch_identity")
        return DispatchIdentity.from_dispatch_id(dispatch_id)
    if "__enum__" in value:
        enum_name = value.get("__enum__")
        enum_value = value.get("value")
        if (
            not isinstance(enum_name, str)
            or enum_name not in _ENUM_REGISTRY
            or not isinstance(enum_value, str)
        ):
            _raise_invalid("unknown_serialized_enum")
        try:
            return _ENUM_REGISTRY[enum_name](enum_value)
        except (TypeError, ValueError) as exc:
            raise ContextAdmissionValidationError("invalid_serialized_enum") from exc
    if "__tuple__" in value:
        raw = value["__tuple__"]
        if not isinstance(raw, list):
            _raise_invalid("invalid_serialized_tuple")
        return tuple(_decode(item) for item in raw)
    if "__frozenset__" in value:
        raw = value["__frozenset__"]
        if not isinstance(raw, list):
            _raise_invalid("invalid_serialized_frozenset")
        return frozenset(_decode(item) for item in raw)
    type_name = value.get("__type__")
    if type_name == "ModelIdentity":
        try:
            return ModelIdentity(
                configured_model=str(value["configured_model"]),
                effective_model=str(value["effective_model"]),
                profile_name=str(value["profile_name"]),
            )
        except KeyError as exc:
            raise ContextAdmissionValidationError("invalid_model_identity") from exc
    if not isinstance(type_name, str) or type_name not in _TYPE_REGISTRY:
        _raise_invalid("unknown_serialized_contract_type")
    contract_type = _TYPE_REGISTRY[type_name]
    kwargs = {key: _decode(item) for key, item in value.items() if key != "__type__"}
    try:
        return contract_type(**kwargs)
    except TypeError as exc:
        raise ContextAdmissionValidationError("invalid_serialized_contract") from exc


class _ContractMeta(type):
    def __call__(cls, *args: Any, **kwargs: Any) -> Any:
        instance = super().__call__(*args, **kwargs)
        _validate_deep_immutability(instance)
        return instance


class _ContractValue(metaclass=_ContractMeta):
    """Canonical content-free serialization shared by all protocol values."""

    _registry: ClassVar[dict[str, type[_ContractValue]]] = _TYPE_REGISTRY
    __dataclass_fields__: ClassVar[dict[str, Any]]

    def __init_subclass__(cls) -> None:
        super().__init_subclass__()
        _TYPE_REGISTRY[cls.__name__] = cls

    def to_dict(self) -> dict[str, object]:
        encoded = _encode(self)
        if not isinstance(encoded, dict):
            _raise_invalid("invalid_contract_serialization")
        encoded.pop("__type__", None)
        return encoded

    @classmethod
    def from_dict(cls, data: Mapping[str, object]) -> Any:
        if not isinstance(data, Mapping):
            _raise_invalid("invalid_serialized_contract")
        tagged = {"__type__": cls.__name__, **dict(data)}
        decoded = _decode(tagged)
        if not isinstance(decoded, cls):
            _raise_invalid("serialized_contract_type_mismatch")
        return decoded


def _validate_deep_immutability(value: object) -> None:
    if isinstance(value, list | dict | set):
        _raise_invalid("mutable_contract_collection")
    if isinstance(value, tuple | frozenset):
        for item in value:
            _validate_deep_immutability(item)
    elif isinstance(value, _ContractValue):
        for field_name in value.__dataclass_fields__:
            _validate_deep_immutability(getattr(value, field_name))


@dataclass(frozen=True, slots=True)
class _OpaqueString(_ContractValue):
    value: str

    def __post_init__(self) -> None:
        _validate_bounded_text(self.value, "invalid_opaque_identifier")
        allowed = "-_.:"
        if any(
            not (character.isascii() and (character.isalnum() or character in allowed))
            for character in self.value
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


@dataclass(frozen=True, slots=True)
class ContextWindowSnapshot(_ContractValue):
    protocol_version: int
    window_epoch_id: WindowEpochId
    window_epoch_number: int
    model_identity: ModelIdentity
    tokenizer_identity: TokenizerIdentity
    snapshot_sequence: int
    active_count: int
    hard_limit: int
    remaining_count: int

    def __post_init__(self) -> None:
        _validate_protocol_version(self.protocol_version)
        for value, reason in (
            (self.window_epoch_number, "invalid_window_epoch_number"),
            (self.snapshot_sequence, "invalid_snapshot_sequence"),
            (self.active_count, "invalid_active_count"),
            (self.hard_limit, "invalid_hard_limit"),
            (self.remaining_count, "invalid_remaining_count"),
        ):
            _validate_non_negative(value, reason)
        if (
            not self.model_identity.configured_model
            or not self.model_identity.effective_model
            or self.active_count + self.remaining_count > self.hard_limit
        ):
            _raise_invalid("invalid_authoritative_snapshot")


@dataclass(frozen=True, slots=True)
class CanonicalSpanOwner(_ContractValue):
    span_id: CanonicalSpanId
    occurrence_id: AdmissionOccurrenceId


@dataclass(frozen=True, slots=True)
class CanonicalRepresentationManifest(_ContractValue):
    request_id: AdmissionRequestId
    representation_revision: RepresentationRevision
    span_owners: tuple[CanonicalSpanOwner, ...]
    assembler_identity: ProducerInstanceId
    assembler_witness_id: AdmissionWitnessId

    def __post_init__(self) -> None:
        _validate_tuple(self.span_owners, "invalid_span_owners")
        if not self.span_owners:
            _raise_invalid("incomplete_representation_manifest")
        span_ids = tuple(owner.span_id for owner in self.span_owners)
        if len(span_ids) != len(set(span_ids)):
            _raise_invalid("overlapping_canonical_span_ownership")


@dataclass(frozen=True, slots=True)
class AdmissionOccurrence(_ContractValue):
    occurrence_id: AdmissionOccurrenceId
    lineage: ContextLineage
    reserve_class: ReserveClass
    producer_surface: ProducerSurface
    predicted_authoritative_maximum: int
    representation_revision: RepresentationRevision
    owned_span_ids: tuple[CanonicalSpanId, ...]

    def __post_init__(self) -> None:
        _validate_non_negative(
            self.predicted_authoritative_maximum,
            "invalid_predicted_authoritative_maximum",
        )
        _validate_tuple(self.owned_span_ids, "invalid_owned_span_ids")
        if not self.owned_span_ids or len(self.owned_span_ids) != len(set(self.owned_span_ids)):
            _raise_invalid("invalid_owned_span_ids")
        if self.producer_surface is not self.lineage.producer_surface:
            _raise_invalid("producer_surface_mismatch")


@dataclass(frozen=True, slots=True)
class AdmissionBatch(_ContractValue):
    batch_id: AdmissionBatchId
    request_id: AdmissionRequestId
    occurrence_ids: tuple[AdmissionOccurrenceId, ...]
    reserve_class: ReserveClass
    protected_pool_owner_id: ProtectedPoolOwnerId | None
    manifest: CanonicalRepresentationManifest

    def __post_init__(self) -> None:
        _validate_tuple(self.occurrence_ids, "invalid_batch_occurrences")
        if not self.occurrence_ids or len(self.occurrence_ids) != len(set(self.occurrence_ids)):
            _raise_invalid("invalid_batch_occurrences")
        if self.manifest.request_id != self.request_id:
            _raise_invalid("batch_manifest_request_mismatch")
        manifest_occurrences = tuple(owner.occurrence_id for owner in self.manifest.span_owners)
        if set(manifest_occurrences) != set(self.occurrence_ids):
            _raise_invalid("incomplete_representation_manifest")
        if (self.reserve_class is ReserveClass.ORDINARY) != (self.protected_pool_owner_id is None):
            _raise_invalid("invalid_protected_pool_owner")


@dataclass(frozen=True, slots=True)
class AdmissionReservationKey(_ContractValue):
    idempotency_namespace: IdempotencyNamespace
    protocol_version: int
    window_epoch_id: WindowEpochId
    window_epoch_number: int
    batch_id: AdmissionBatchId
    reserve_class: ReserveClass
    protected_pool_owner_id: ProtectedPoolOwnerId | None
    occurrence_revisions: tuple[tuple[AdmissionOccurrenceId, RepresentationRevision], ...]

    def __post_init__(self) -> None:
        _validate_protocol_version(self.protocol_version)
        _validate_non_negative(self.window_epoch_number, "invalid_window_epoch_number")
        _validate_tuple(self.occurrence_revisions, "invalid_occurrence_revisions")
        occurrence_ids = tuple(pair[0] for pair in self.occurrence_revisions)
        if (
            not occurrence_ids
            or len(occurrence_ids) != len(set(occurrence_ids))
            or (self.reserve_class is ReserveClass.ORDINARY)
            != (self.protected_pool_owner_id is None)
        ):
            _raise_invalid("invalid_reservation_key")


@dataclass(frozen=True, slots=True)
class AdmissionReservation(_ContractValue):
    reservation_id: AdmissionReservationId
    key: AdmissionReservationKey
    window_epoch_id: WindowEpochId
    window_epoch_number: int
    snapshot_sequence: int
    reserve_class: ReserveClass
    protected_pool_owner_id: ProtectedPoolOwnerId | None
    occurrence_ids: tuple[AdmissionOccurrenceId, ...]
    reserved_count: int

    def __post_init__(self) -> None:
        _validate_non_negative(self.window_epoch_number, "invalid_window_epoch_number")
        _validate_non_negative(self.snapshot_sequence, "invalid_snapshot_sequence")
        _validate_non_negative(self.reserved_count, "invalid_reserved_count")
        _validate_tuple(self.occurrence_ids, "invalid_reservation_occurrences")
        if (
            self.window_epoch_id != self.key.window_epoch_id
            or self.window_epoch_number != self.key.window_epoch_number
            or self.reserve_class is not self.key.reserve_class
            or self.protected_pool_owner_id != self.key.protected_pool_owner_id
            or self.occurrence_ids
            != tuple(occurrence_id for occurrence_id, _ in self.key.occurrence_revisions)
        ):
            _raise_invalid("reservation_key_mismatch")


@dataclass(frozen=True, slots=True)
class AdmissionWitness(_ContractValue):
    witness_id: AdmissionWitnessId
    kind: WitnessKind
    window_epoch_id: WindowEpochId
    window_epoch_number: int
    snapshot_sequence: int
    request_id: AdmissionRequestId
    batch_id: AdmissionBatchId
    representation_revision: RepresentationRevision
    occurrence_ids: tuple[AdmissionOccurrenceId, ...]
    authority_source_id: AuthoritySourceId

    def __post_init__(self) -> None:
        _validate_non_negative(self.window_epoch_number, "invalid_window_epoch_number")
        _validate_non_negative(self.snapshot_sequence, "invalid_snapshot_sequence")
        _validate_tuple(self.occurrence_ids, "invalid_witness_occurrences")


@dataclass(frozen=True, slots=True)
class RepresentationBindingWitness(_ContractValue):
    counted_representation_revision: RepresentationRevision
    dispatched_representation_revision: RepresentationRevision
    final_manifest_revision: RepresentationRevision
    request_id: AdmissionRequestId
    batch_id: AdmissionBatchId
    authority_source_id: AuthoritySourceId


@dataclass(frozen=True, slots=True)
class EpochFenceProof(_ContractValue):
    old_window_epoch_id: WindowEpochId
    old_window_epoch_number: int
    new_window_epoch_id: WindowEpochId
    new_window_epoch_number: int
    receiver_authority_source_id: AuthoritySourceId
    fence_witness_id: AdmissionWitnessId
    highest_admitted_dispatch_sequence: int

    def __post_init__(self) -> None:
        _validate_non_negative(self.old_window_epoch_number, "invalid_window_epoch_number")
        _validate_non_negative(self.new_window_epoch_number, "invalid_window_epoch_number")
        _validate_non_negative(
            self.highest_admitted_dispatch_sequence,
            "invalid_dispatch_sequence",
        )


@dataclass(frozen=True, slots=True)
class ProtectedPoolSpec(_ContractValue):
    reserve_class: ReserveClass
    capability_owner_id: ProtectedPoolOwnerId
    injected_count: int
    priority: int
    required_release_witness_kind: WitnessKind

    def __post_init__(self) -> None:
        _validate_non_negative(self.injected_count, "invalid_protected_pool_count")
        _validate_non_negative(self.priority, "invalid_protected_pool_priority")
        if self.reserve_class is ReserveClass.ORDINARY:
            _raise_invalid("ordinary_pool_forbidden")


@dataclass(frozen=True, slots=True)
class AdmissionDecision(_ContractValue):
    kind: AdmissionDecisionKind
    reason_code: str
    window_epoch_id: WindowEpochId | None
    snapshot_sequence: int | None
    requested_count: int
    available_ordinary_count: int
    available_protected_count: int

    def __post_init__(self) -> None:
        _validate_bounded_text(self.reason_code, "invalid_reason_code", maximum=64)
        for value in (
            self.requested_count,
            self.available_ordinary_count,
            self.available_protected_count,
        ):
            _validate_non_negative(value, "invalid_decision_count")
        if self.snapshot_sequence is not None:
            _validate_non_negative(self.snapshot_sequence, "invalid_snapshot_sequence")


@dataclass(frozen=True, slots=True)
class AdmissionOccurrenceRecord(_ContractValue):
    occurrence: AdmissionOccurrence
    state: AdmissionState
    batch_id: AdmissionBatchId | None
    reservation_id: AdmissionReservationId | None
    accepted_witness_ids: tuple[AdmissionWitnessId, ...]
    indeterminate_reason_code: str | None
    quarantine_reason_code: str | None

    def __post_init__(self) -> None:
        _validate_tuple(self.accepted_witness_ids, "invalid_witness_ids")
        if len(self.accepted_witness_ids) != len(set(self.accepted_witness_ids)):
            _raise_invalid("duplicate_witness_id")
        if self.indeterminate_reason_code is not None:
            _validate_bounded_text(
                self.indeterminate_reason_code,
                "invalid_indeterminate_reason",
                maximum=64,
            )
        if self.quarantine_reason_code is not None:
            _validate_bounded_text(
                self.quarantine_reason_code,
                "invalid_quarantine_reason",
                maximum=64,
            )


@dataclass(frozen=True, slots=True)
class AdmissionBatchRecord(_ContractValue):
    batch: AdmissionBatch
    state: AdmissionState
    reservation_id: AdmissionReservationId | None
    witness_ids: tuple[AdmissionWitnessId, ...]
    prepared_input_count: int | None
    committed_input_count: int
    unresolved_input_count: int

    def __post_init__(self) -> None:
        _validate_tuple(self.witness_ids, "invalid_witness_ids")
        if len(self.witness_ids) != len(set(self.witness_ids)):
            _raise_invalid("duplicate_witness_id")
        if self.prepared_input_count is not None:
            _validate_non_negative(self.prepared_input_count, "invalid_prepared_count")
        _validate_non_negative(self.committed_input_count, "invalid_committed_count")
        _validate_non_negative(self.unresolved_input_count, "invalid_unresolved_count")
        if self.committed_input_count > 0 and self.unresolved_input_count > 0:
            _raise_invalid("committed_and_unresolved_simultaneously")


@dataclass(frozen=True, slots=True)
class GenerationReservationRecord(_ContractValue):
    generation_reservation_id: GenerationReservationId
    request_id: AdmissionRequestId
    response_id: ModelItemId
    window_epoch_id: WindowEpochId
    window_epoch_number: int
    snapshot_sequence: int
    reserve_class: ReserveClass
    protected_pool_owner_id: ProtectedPoolOwnerId | None
    maximum_allowance: int
    state: GenerationState
    exact_terminal_usage: int | None
    witness_ids: tuple[AdmissionWitnessId, ...]

    def __post_init__(self) -> None:
        for value in (
            self.window_epoch_number,
            self.snapshot_sequence,
            self.maximum_allowance,
        ):
            _validate_non_negative(value, "invalid_generation_reservation")
        if self.exact_terminal_usage is not None:
            _validate_non_negative(self.exact_terminal_usage, "invalid_generation_usage")
        if (self.reserve_class is ReserveClass.ORDINARY) != (self.protected_pool_owner_id is None):
            _raise_invalid("invalid_generation_owner")


@dataclass(frozen=True, slots=True)
class ExpiredIdempotencyTombstone(_ContractValue):
    namespace: IdempotencyNamespace
    reservation_key: AdmissionReservationKey
    original_descriptor: ReserveRequestEvent
    expiry_witness: AdmissionWitness
    original_terminal_decision: AdmissionDecision


@dataclass(frozen=True, slots=True)
class ClosedEpochAudit(_ContractValue):
    snapshot: ContextWindowSnapshot
    terminal_occurrence_records: tuple[AdmissionOccurrenceRecord, ...]
    terminal_reservations: tuple[AdmissionReservation, ...]
    closure_witness_id: AdmissionWitnessId
    fence_proof: EpochFenceProof | None
    processed_event_tombstones: tuple[AdmissionEventId, ...]
    retained_unresolved_count: int

    def __post_init__(self) -> None:
        _validate_non_negative(self.retained_unresolved_count, "invalid_retained_charge")


@dataclass(frozen=True, slots=True)
class CoverageEvidence(_ContractValue):
    claim_id: str
    kind: CoverageEvidenceKind
    backend: str
    configuration_mode: str
    verifier: str
    source_locator: str
    tested_version: str
    tested_revision: str
    checked_at: str
    freshness_policy: str

    def __post_init__(self) -> None:
        for value, reason, maximum in (
            (self.claim_id, "invalid_claim_id", 96),
            (self.backend, "invalid_evidence_backend", 64),
            (self.configuration_mode, "invalid_configuration_mode", 64),
            (self.verifier, "invalid_evidence_verifier", 64),
            (self.source_locator, "invalid_source_locator", 256),
            (self.tested_version, "invalid_tested_version", 64),
            (self.tested_revision, "invalid_tested_revision", 96),
            (self.checked_at, "invalid_checked_at", 32),
            (self.freshness_policy, "invalid_freshness_policy", 128),
        ):
            _validate_bounded_text(value, reason, maximum=maximum)


@dataclass(frozen=True, slots=True)
class ProducerCoverageDef(_ContractValue):
    surface: ProducerSurface
    control_point_owner: str
    observation_state: CoverageState
    authority_state: CoverageState
    evidence: tuple[CoverageEvidence, ...]
    reason_code: str

    def __post_init__(self) -> None:
        _validate_bounded_text(
            self.control_point_owner,
            "invalid_control_point_owner",
            maximum=96,
        )
        _validate_bounded_text(self.reason_code, "invalid_reason_code", maximum=64)
        _validate_tuple(self.evidence, "invalid_coverage_evidence")
        if not self.evidence:
            _raise_invalid("coverage_evidence_required")
        primary = tuple(
            item for item in self.evidence if item.kind is not CoverageEvidenceKind.INFERENCE
        )
        if (
            self.observation_state is CoverageState.VERIFIED
            or self.authority_state is CoverageState.VERIFIED
        ) and not primary:
            _raise_invalid("verified_coverage_requires_primary_evidence")


@dataclass(frozen=True, slots=True)
class _AdmissionEventBase(_ContractValue):
    event_id: AdmissionEventId
    protocol_version: int
    idempotency_namespace: IdempotencyNamespace
    expected_aggregate_revision: AggregateRevision

    def __post_init__(self) -> None:
        _validate_protocol_version(self.protocol_version)


@dataclass(frozen=True, slots=True)
class OpenEpochEvent(_AdmissionEventBase):
    snapshot: ContextWindowSnapshot
    protected_pools: tuple[ProtectedPoolSpec, ...]


@dataclass(frozen=True, slots=True)
class AuthorityUnavailableEvent(_AdmissionEventBase):
    reason_code: str
    authority_state: CoverageState

    def __post_init__(self) -> None:
        _AdmissionEventBase.__post_init__(self)
        _validate_bounded_text(self.reason_code, "invalid_reason_code", maximum=64)
        if self.authority_state is CoverageState.VERIFIED:
            _raise_invalid("invalid_unavailable_authority_state")


@dataclass(frozen=True, slots=True)
class ProposeOccurrenceEvent(_AdmissionEventBase):
    occurrence: AdmissionOccurrence


@dataclass(frozen=True, slots=True)
class ReserveRequestEvent(_AdmissionEventBase):
    batch: AdmissionBatch
    snapshot_sequence: int
    input_reservations: tuple[AdmissionReservation, ...]
    generation_reservation: GenerationReservationRecord | None

    def __post_init__(self) -> None:
        _AdmissionEventBase.__post_init__(self)
        _validate_non_negative(self.snapshot_sequence, "invalid_snapshot_sequence")
        _validate_tuple(self.input_reservations, "invalid_input_reservations")
        reservation_ids = tuple(
            reservation.reservation_id for reservation in self.input_reservations
        )
        if len(reservation_ids) != len(set(reservation_ids)):
            _raise_invalid("duplicate_reservation_id")
        if self.batch.occurrence_ids != self.input_reservations[0].occurrence_ids:
            _raise_invalid("reservation_occurrence_mismatch")
        if self.idempotency_namespace != self.input_reservations[0].key.idempotency_namespace:
            _raise_invalid("reservation_namespace_mismatch")


@dataclass(frozen=True, slots=True)
class PrepareBatchEvent(_AdmissionEventBase):
    batch_id: AdmissionBatchId
    representation_revision: RepresentationRevision
    proposed_charge: int
    measurement_kind: MeasurementKind
    authority_source_id: AuthoritySourceId

    def __post_init__(self) -> None:
        _AdmissionEventBase.__post_init__(self)
        _validate_non_negative(self.proposed_charge, "invalid_proposed_charge")
        if self.measurement_kind in {
            MeasurementKind.HOST_ESTIMATE,
            MeasurementKind.BYTE_EMERGENCY,
        }:
            _raise_invalid("non_authoritative_measurement")


@dataclass(frozen=True, slots=True)
class StageHistoryEvent(_AdmissionEventBase):
    batch_id: AdmissionBatchId
    witness: AdmissionWitness


@dataclass(frozen=True, slots=True)
class DispatchRequestEvent(_AdmissionEventBase):
    batch_id: AdmissionBatchId
    witness: AdmissionWitness


@dataclass(frozen=True, slots=True)
class AcceptInputEvent(_AdmissionEventBase):
    batch_id: AdmissionBatchId
    witness: AdmissionWitness
    final_manifest_revision: RepresentationRevision
    exact_input_charge: int
    measurement_kind: MeasurementKind
    authority_source_id: AuthoritySourceId
    representation_binding_witness: RepresentationBindingWitness

    def __post_init__(self) -> None:
        _AdmissionEventBase.__post_init__(self)
        _validate_non_negative(self.exact_input_charge, "invalid_exact_input_charge")
        if self.measurement_kind in {
            MeasurementKind.HOST_ESTIMATE,
            MeasurementKind.BYTE_EMERGENCY,
        }:
            _raise_invalid("non_authoritative_measurement")


@dataclass(frozen=True, slots=True)
class ReleaseNonAdmissionEvent(_AdmissionEventBase):
    batch_id: AdmissionBatchId
    witness: AdmissionWitness


@dataclass(frozen=True, slots=True)
class RollbackAdmissionEvent(_AdmissionEventBase):
    batch_id: AdmissionBatchId
    witness: AdmissionWitness


@dataclass(frozen=True, slots=True)
class MarkIndeterminateEvent(_AdmissionEventBase):
    batch_id: AdmissionBatchId
    reason_code: str

    def __post_init__(self) -> None:
        _AdmissionEventBase.__post_init__(self)
        _validate_bounded_text(self.reason_code, "invalid_reason_code", maximum=64)


@dataclass(frozen=True, slots=True)
class ResolveIndeterminateAcceptedEvent(_AdmissionEventBase):
    batch_id: AdmissionBatchId
    witness: AdmissionWitness
    exact_charge: int
    measurement_kind: MeasurementKind
    authority_source_id: AuthoritySourceId

    def __post_init__(self) -> None:
        _AdmissionEventBase.__post_init__(self)
        _validate_non_negative(self.exact_charge, "invalid_exact_charge")
        if self.measurement_kind in {
            MeasurementKind.HOST_ESTIMATE,
            MeasurementKind.BYTE_EMERGENCY,
        }:
            _raise_invalid("non_authoritative_measurement")


@dataclass(frozen=True, slots=True)
class ResolveIndeterminateNonAdmissionEvent(_AdmissionEventBase):
    batch_id: AdmissionBatchId
    witness: AdmissionWitness


@dataclass(frozen=True, slots=True)
class ResolveIndeterminateRollbackEvent(_AdmissionEventBase):
    batch_id: AdmissionBatchId
    witness: AdmissionWitness


@dataclass(frozen=True, slots=True)
class StartGenerationEvent(_AdmissionEventBase):
    generation_reservation_id: GenerationReservationId
    witness: AdmissionWitness


@dataclass(frozen=True, slots=True)
class ReconcileGenerationEvent(_AdmissionEventBase):
    generation_reservation_id: GenerationReservationId
    output_usage_witness: AdmissionWitness
    exact_output_usage: int

    def __post_init__(self) -> None:
        _AdmissionEventBase.__post_init__(self)
        _validate_non_negative(self.exact_output_usage, "invalid_exact_output_usage")


@dataclass(frozen=True, slots=True)
class MarkGenerationIndeterminateEvent(_AdmissionEventBase):
    generation_reservation_id: GenerationReservationId
    reason_code: str

    def __post_init__(self) -> None:
        _AdmissionEventBase.__post_init__(self)
        _validate_bounded_text(self.reason_code, "invalid_reason_code", maximum=64)


@dataclass(frozen=True, slots=True)
class RequestReconciliationEvent(_AdmissionEventBase):
    target_id: AdmissionBatchId | GenerationReservationId
    reason_code: str

    def __post_init__(self) -> None:
        _AdmissionEventBase.__post_init__(self)
        _validate_bounded_text(self.reason_code, "invalid_reason_code", maximum=64)


@dataclass(frozen=True, slots=True)
class ExpireIdempotencyKeyEvent(_AdmissionEventBase):
    reservation_key: AdmissionReservationKey
    expiry_witness: AdmissionWitness


@dataclass(frozen=True, slots=True)
class RolloverEpochEvent(_AdmissionEventBase):
    witness: AdmissionWitness
    fence_proof: EpochFenceProof
    new_snapshot: ContextWindowSnapshot
    protected_pools: tuple[ProtectedPoolSpec, ...]

    def __post_init__(self) -> None:
        _AdmissionEventBase.__post_init__(self)
        _validate_tuple(self.protected_pools, "invalid_protected_pools")


ContextAdmissionEvent: TypeAlias = (
    OpenEpochEvent
    | AuthorityUnavailableEvent
    | ProposeOccurrenceEvent
    | ReserveRequestEvent
    | PrepareBatchEvent
    | StageHistoryEvent
    | DispatchRequestEvent
    | AcceptInputEvent
    | ReleaseNonAdmissionEvent
    | RollbackAdmissionEvent
    | MarkIndeterminateEvent
    | ResolveIndeterminateAcceptedEvent
    | ResolveIndeterminateNonAdmissionEvent
    | ResolveIndeterminateRollbackEvent
    | StartGenerationEvent
    | ReconcileGenerationEvent
    | MarkGenerationIndeterminateEvent
    | RequestReconciliationEvent
    | ExpireIdempotencyKeyEvent
    | RolloverEpochEvent
)


@dataclass(frozen=True, slots=True)
class _AdmissionEffectBase(_ContractValue):
    source_event_id: AdmissionEventId
    resulting_aggregate_revision: AggregateRevision
    resulting_admission_sequence: AdmissionSequence
    target_id: (
        AdmissionOccurrenceId
        | AdmissionBatchId
        | AdmissionReservationId
        | GenerationReservationId
        | WindowEpochId
    )


@dataclass(frozen=True, slots=True)
class ReservationRecordedEffect(_AdmissionEffectBase):
    charge_domain: ChargeDomain
    reserve_class: ReserveClass
    protected_pool_owner_id: ProtectedPoolOwnerId | None
    count: int
    window_epoch_id: WindowEpochId
    snapshot_sequence: int
    witness_ids: tuple[AdmissionWitnessId, ...]


@dataclass(frozen=True, slots=True)
class ReservationReleasedEffect(_AdmissionEffectBase):
    charge_domain: ChargeDomain
    reserve_class: ReserveClass
    protected_pool_owner_id: ProtectedPoolOwnerId | None
    count: int
    window_epoch_id: WindowEpochId
    snapshot_sequence: int
    witness_ids: tuple[AdmissionWitnessId, ...]


@dataclass(frozen=True, slots=True)
class OccurrenceStateChangedEffect(_AdmissionEffectBase):
    previous_state: AdmissionState
    next_state: AdmissionState


@dataclass(frozen=True, slots=True)
class ChargeCommittedEffect(_AdmissionEffectBase):
    charge_domain: ChargeDomain
    reserve_class: ReserveClass
    protected_pool_owner_id: ProtectedPoolOwnerId | None
    count: int
    window_epoch_id: WindowEpochId
    snapshot_sequence: int
    witness_ids: tuple[AdmissionWitnessId, ...]


@dataclass(frozen=True, slots=True)
class GenerationReservationRecordedEffect(_AdmissionEffectBase):
    charge_domain: ChargeDomain
    reserve_class: ReserveClass
    protected_pool_owner_id: ProtectedPoolOwnerId | None
    count: int
    window_epoch_id: WindowEpochId
    snapshot_sequence: int
    witness_ids: tuple[AdmissionWitnessId, ...]


@dataclass(frozen=True, slots=True)
class GenerationReconciledEffect(_AdmissionEffectBase):
    charge_domain: ChargeDomain
    reserve_class: ReserveClass
    protected_pool_owner_id: ProtectedPoolOwnerId | None
    count: int
    window_epoch_id: WindowEpochId
    snapshot_sequence: int
    witness_ids: tuple[AdmissionWitnessId, ...]


@dataclass(frozen=True, slots=True)
class ReconciliationQueryRequestedEffect(_AdmissionEffectBase):
    reason_code: str


@dataclass(frozen=True, slots=True)
class ReconciliationEscalationEffect(_AdmissionEffectBase):
    reason_code: str


@dataclass(frozen=True, slots=True)
class ConflictRejectedEffect(_AdmissionEffectBase):
    reason_code: str


@dataclass(frozen=True, slots=True)
class IdempotencyExpiredEffect(_AdmissionEffectBase):
    reservation_key: AdmissionReservationKey
    expiry_witness_id: AdmissionWitnessId


@dataclass(frozen=True, slots=True)
class ReservationInvalidatedEffect(_AdmissionEffectBase):
    charge_domain: ChargeDomain
    reserve_class: ReserveClass
    protected_pool_owner_id: ProtectedPoolOwnerId | None
    count: int
    window_epoch_id: WindowEpochId
    snapshot_sequence: int
    witness_ids: tuple[AdmissionWitnessId, ...]


@dataclass(frozen=True, slots=True)
class EpochClosedEffect(_AdmissionEffectBase):
    fence_proof: EpochFenceProof


@dataclass(frozen=True, slots=True)
class QuarantineRecordedEffect(_AdmissionEffectBase):
    reason_code: str


@dataclass(frozen=True, slots=True)
class AuthorityUnavailableEffect(_AdmissionEffectBase):
    reason_code: str
    authority_state: CoverageState


AdmissionEffect: TypeAlias = (
    ReservationRecordedEffect
    | ReservationReleasedEffect
    | OccurrenceStateChangedEffect
    | ChargeCommittedEffect
    | GenerationReservationRecordedEffect
    | GenerationReconciledEffect
    | ReconciliationQueryRequestedEffect
    | ReconciliationEscalationEffect
    | ConflictRejectedEffect
    | IdempotencyExpiredEffect
    | ReservationInvalidatedEffect
    | EpochClosedEffect
    | QuarantineRecordedEffect
    | AuthorityUnavailableEffect
)


@dataclass(frozen=True, slots=True)
class ProcessedEventRecord(_ContractValue):
    event_id: AdmissionEventId
    event: ContextAdmissionEvent
    original_decision: AdmissionDecision
    aggregate_revision: AggregateRevision
    admission_sequence: AdmissionSequence


@dataclass(frozen=True, slots=True)
class IdempotencyRecord(_ContractValue):
    namespace: IdempotencyNamespace
    reservation_key: AdmissionReservationKey
    original_descriptor: ReserveRequestEvent
    original_reserve_decision: AdmissionDecision
    owning_event_id: AdmissionEventId
    publication_revision: AggregateRevision


@dataclass(frozen=True, slots=True)
class UninitializedContextAdmissionState(_ContractValue):
    protocol_version: int
    aggregate_revision: AggregateRevision
    admission_sequence: AdmissionSequence
    processed_events: tuple[ProcessedEventRecord, ...]
    idempotency_records: tuple[IdempotencyRecord, ...]
    expired_idempotency_tombstones: tuple[ExpiredIdempotencyTombstone, ...]
    closed_epochs: tuple[ClosedEpochAudit, ...]

    def __post_init__(self) -> None:
        _validate_protocol_version(self.protocol_version)


@dataclass(frozen=True, slots=True)
class ActiveContextAdmissionState(_ContractValue):
    protocol_version: int
    aggregate_revision: AggregateRevision
    admission_sequence: AdmissionSequence
    snapshot: ContextWindowSnapshot
    protected_pools: tuple[ProtectedPoolSpec, ...]
    occurrence_records: tuple[AdmissionOccurrenceRecord, ...]
    batch_records: tuple[AdmissionBatchRecord, ...]
    reservations: tuple[AdmissionReservation, ...]
    generation_reservations: tuple[GenerationReservationRecord, ...]
    processed_events: tuple[ProcessedEventRecord, ...]
    idempotency_records: tuple[IdempotencyRecord, ...]
    expired_idempotency_tombstones: tuple[ExpiredIdempotencyTombstone, ...]
    closed_epochs: tuple[ClosedEpochAudit, ...]

    def __post_init__(self) -> None:
        _validate_protocol_version(self.protocol_version)
        if self.snapshot.protocol_version != self.protocol_version:
            _raise_invalid("state_snapshot_protocol_mismatch")
        pools = tuple(
            (pool.reserve_class, pool.capability_owner_id) for pool in self.protected_pools
        )
        if len(pools) != len(set(pools)):
            _raise_invalid("duplicate_protected_pool")
        if sum(pool.injected_count for pool in self.protected_pools) > (
            self.snapshot.remaining_count
        ):
            _raise_invalid("protected_pool_capacity_exceeded")
        _validate_tuple(self.batch_records, "invalid_batch_records")
        _validate_tuple(self.reservations, "invalid_reservations")
        _validate_tuple(self.generation_reservations, "invalid_generation_reservations")
        _validate_tuple(self.occurrence_records, "invalid_occurrence_records")
        batch_ids = tuple(record.batch.batch_id for record in self.batch_records)
        if len(batch_ids) != len(set(batch_ids)):
            _raise_invalid("duplicate_batch_owner")
        reservation_ids = tuple(reservation.reservation_id for reservation in self.reservations)
        if len(reservation_ids) != len(set(reservation_ids)):
            _raise_invalid("duplicate_reservation_owner")
        generation_ids = tuple(
            record.generation_reservation_id for record in self.generation_reservations
        )
        if len(generation_ids) != len(set(generation_ids)):
            _raise_invalid("duplicate_generation_owner")
        occurrence_ids = tuple(
            record.occurrence.occurrence_id for record in self.occurrence_records
        )
        if len(occurrence_ids) != len(set(occurrence_ids)):
            _raise_invalid("duplicate_occurrence_owner")
        seen_pool_owners: set[tuple[ReserveClass, ProtectedPoolOwnerId]] = set()
        seen_batch_owners: set[tuple[ReserveClass, ProtectedPoolOwnerId]] = set()
        seen_generation_owners: set[tuple[ReserveClass, ProtectedPoolOwnerId]] = set()
        for record in self.batch_records:
            owner = record.batch.protected_pool_owner_id
            if record.batch.reserve_class is not ReserveClass.ORDINARY:
                if owner is None:
                    _raise_invalid("missing_protected_pool_owner")
                key = (record.batch.reserve_class, owner)
                if key in seen_batch_owners:
                    _raise_invalid("duplicate_protected_charge_owner")
                seen_batch_owners.add(key)
                seen_pool_owners.add(key)
                if not any(
                    pool.reserve_class is record.batch.reserve_class
                    and pool.capability_owner_id == owner
                    for pool in self.protected_pools
                ):
                    _raise_invalid("orphan_protected_charge_owner")
        for generation_record in self.generation_reservations:
            owner = generation_record.protected_pool_owner_id
            if generation_record.reserve_class is not ReserveClass.ORDINARY:
                if owner is None:
                    _raise_invalid("missing_protected_pool_owner")
                key = (generation_record.reserve_class, owner)
                if key in seen_generation_owners:
                    _raise_invalid("duplicate_protected_charge_owner")
                seen_generation_owners.add(key)
                if not any(
                    pool.reserve_class is generation_record.reserve_class
                    and pool.capability_owner_id == owner
                    for pool in self.protected_pools
                ):
                    _raise_invalid("orphan_protected_charge_owner")


ContextAdmissionState: TypeAlias = UninitializedContextAdmissionState | ActiveContextAdmissionState


@dataclass(frozen=True, slots=True)
class AdmissionTransition(_ContractValue):
    next_state: ContextAdmissionState
    decision: AdmissionDecision
    effects: tuple[AdmissionEffect, ...]


@dataclass(frozen=True, slots=True)
class AdmissionReplay(_ContractValue):
    final_state: ContextAdmissionState
    transitions: tuple[AdmissionTransition, ...]


_VERIFIED_SURFACES = frozenset(
    {
        ProducerSurface.NATIVE_SHELL,
        ProducerSurface.AUTOSKILLIT_MCP,
        ProducerSurface.AUTOSKILLIT_LOCAL_FUNCTION,
        ProducerSurface.HOOK_FEEDBACK,
        ProducerSurface.HEADLESS_CHILD_PROMPT,
        ProducerSurface.PARENT_VISIBLE_CHILD_DELIVERY,
    }
)
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
_UNOBSERVABLE_SURFACES = frozenset(
    {
        ProducerSurface.CLIENT_PROVIDER_RETRIEVAL,
        ProducerSurface.OTHER_CONTEXT_INJECTION,
    }
)
_CONTROL_POINT_OWNERS = {
    ProducerSurface.NATIVE_SHELL: "shell_capture_hook",
    ProducerSurface.AUTOSKILLIT_MCP: "track_response_size",
    ProducerSurface.AUTOSKILLIT_LOCAL_FUNCTION: "local_function_dispatch",
    ProducerSurface.HOOK_FEEDBACK: "hook_registry",
    ProducerSurface.HEADLESS_CHILD_PROMPT: "headless_prompt_builder",
    ProducerSurface.PARENT_VISIBLE_CHILD_DELIVERY: "child_delivery_receipt",
    ProducerSurface.EXTERNAL_MCP: "fastmcp_client",
    ProducerSurface.MCP_RESOURCE: "fastmcp_client",
    ProducerSurface.COMPACTION_MODEL_WINDOW_TRANSITION: "compaction_receiver",
}
_LOCAL_SOURCE_LOCATORS = {
    ProducerSurface.NATIVE_SHELL: "src/autoskillit/hooks/shell_capture_hook.py",
    ProducerSurface.AUTOSKILLIT_MCP: "src/autoskillit/server/_notify.py",
    ProducerSurface.AUTOSKILLIT_LOCAL_FUNCTION: (
        "src/autoskillit/execution/headless/_headless_helpers.py"
    ),
    ProducerSurface.HOOK_FEEDBACK: "src/autoskillit/hook_registry.py",
    ProducerSurface.HEADLESS_CHILD_PROMPT: (
        "src/autoskillit/execution/headless/_headless_helpers.py"
    ),
    ProducerSurface.PARENT_VISIBLE_CHILD_DELIVERY: ("src/autoskillit/server/_recipe_delivery.py"),
}


def _coverage_row(surface: ProducerSurface) -> ProducerCoverageDef:
    if surface in _VERIFIED_SURFACES:
        observation_state = CoverageState.VERIFIED
        evidence_kind = CoverageEvidenceKind.AUTOSKILLIT_SOURCE
        backend = "autoskillit"
        verifier = "source_inspection"
        locator = _LOCAL_SOURCE_LOCATORS[surface]
        version = "0.10.890"
        revision = "ac8f653a00d2"
    elif surface in _UNOBSERVABLE_SURFACES:
        observation_state = CoverageState.UPSTREAM_GATED
        evidence_kind = CoverageEvidenceKind.INFERENCE
        backend = "codex"
        verifier = "source_gap_analysis"
        locator = "docs/decisions/0007-context-admission.md"
        version = "0.145.0"
        revision = "25af12f7e61572b0bc18ddb1008be543b91519b0"
    else:
        observation_state = CoverageState.PARTIAL
        evidence_kind = CoverageEvidenceKind.CODEX_SOURCE
        backend = "codex"
        verifier = "source_inspection"
        locator = "codex-rs/core/src/context_manager/history.rs"
        version = "0.145.0"
        revision = "25af12f7e61572b0bc18ddb1008be543b91519b0"
    owner = _CONTROL_POINT_OWNERS.get(surface)
    if owner is None:
        if surface in {
            ProducerSurface.UNIFIED_EXEC_AND_WRITE_STDIN,
            ProducerSurface.APPLY_PATCH,
            ProducerSurface.OTHER_LOCAL_FUNCTION,
            ProducerSurface.CLIENT_PROVIDER_RETRIEVAL,
            ProducerSurface.CODE_MODE_AGGREGATE,
            ProducerSurface.HOSTED_SPECIALIZED_TOOL,
        }:
            owner = "codex_host"
        else:
            owner = "final_request_assembler"
    claim_id = f"COV-{surface.name.replace('_', '-')}"
    evidence = CoverageEvidence(
        claim_id=claim_id,
        kind=evidence_kind,
        backend=backend,
        configuration_mode="default",
        verifier=verifier,
        source_locator=locator,
        tested_version=version,
        tested_revision=revision,
        checked_at="2026-07-23",
        freshness_policy="verify_on_version_or_configuration_change",
    )
    return ProducerCoverageDef(
        surface=surface,
        control_point_owner=owner,
        observation_state=observation_state,
        authority_state=CoverageState.UPSTREAM_GATED,
        evidence=(evidence,),
        reason_code="authoritative_watermark_unavailable",
    )


CONTEXT_ADMISSION_COVERAGE = tuple(_coverage_row(surface) for surface in ProducerSurface)


__all__ = [
    "CONTEXT_ADMISSION_PROTOCOL_VERSION",
    "CONTEXT_ADMISSION_COVERAGE",
    "ContextAdmissionValidationError",
    "UnsupportedContextAdmissionProtocolError",
    "ContextSessionId",
    "AgentInstanceId",
    "ContextThreadId",
    "ForkOccurrenceId",
    "TurnId",
    "ProducerInstanceId",
    "ToolCallId",
    "ModelItemId",
    "AdmissionRequestId",
    "AdmissionBatchId",
    "WindowEpochId",
    "TokenizerIdentity",
    "CanonicalSpanId",
    "AdmissionOccurrenceId",
    "AdmissionAttemptId",
    "DeliveryOccurrenceId",
    "AdmissionEventId",
    "AdmissionReservationId",
    "AdmissionWitnessId",
    "AuthoritySourceId",
    "GenerationReservationId",
    "ProtectedPoolOwnerId",
    "RepresentationRevision",
    "AggregateRevision",
    "AdmissionSequence",
    "IdempotencyNamespace",
    "ContextLineage",
    "ContextWindowSnapshot",
    "CanonicalSpanOwner",
    "CanonicalRepresentationManifest",
    "AdmissionOccurrence",
    "AdmissionBatch",
    "AdmissionReservationKey",
    "AdmissionReservation",
    "AdmissionWitness",
    "RepresentationBindingWitness",
    "EpochFenceProof",
    "ProtectedPoolSpec",
    "AdmissionDecision",
    "AdmissionOccurrenceRecord",
    "AdmissionBatchRecord",
    "GenerationReservationRecord",
    "ProcessedEventRecord",
    "IdempotencyRecord",
    "ExpiredIdempotencyTombstone",
    "ClosedEpochAudit",
    "CoverageEvidence",
    "ProducerCoverageDef",
    "OpenEpochEvent",
    "AuthorityUnavailableEvent",
    "ProposeOccurrenceEvent",
    "ReserveRequestEvent",
    "PrepareBatchEvent",
    "StageHistoryEvent",
    "DispatchRequestEvent",
    "AcceptInputEvent",
    "ReleaseNonAdmissionEvent",
    "RollbackAdmissionEvent",
    "MarkIndeterminateEvent",
    "ResolveIndeterminateAcceptedEvent",
    "ResolveIndeterminateNonAdmissionEvent",
    "ResolveIndeterminateRollbackEvent",
    "StartGenerationEvent",
    "ReconcileGenerationEvent",
    "MarkGenerationIndeterminateEvent",
    "RequestReconciliationEvent",
    "ExpireIdempotencyKeyEvent",
    "RolloverEpochEvent",
    "ContextAdmissionEvent",
    "ReservationRecordedEffect",
    "ReservationReleasedEffect",
    "OccurrenceStateChangedEffect",
    "ChargeCommittedEffect",
    "GenerationReservationRecordedEffect",
    "GenerationReconciledEffect",
    "ReconciliationQueryRequestedEffect",
    "ReconciliationEscalationEffect",
    "ConflictRejectedEffect",
    "IdempotencyExpiredEffect",
    "ReservationInvalidatedEffect",
    "EpochClosedEffect",
    "QuarantineRecordedEffect",
    "AuthorityUnavailableEffect",
    "AdmissionEffect",
    "UninitializedContextAdmissionState",
    "ActiveContextAdmissionState",
    "ContextAdmissionState",
    "AdmissionTransition",
    "AdmissionReplay",
]
