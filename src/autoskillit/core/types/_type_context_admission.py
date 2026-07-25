"""Pure protocol-v1 values for cumulative context admission.

The contract is intentionally content-free and implementation-independent.  It
contains immutable commands, records, decisions, and declarative publication
effects; persistence and producer integration belong to downstream layers.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field, fields
from enum import StrEnum
from typing import (
    Any,
    ClassVar,
    TypeAlias,
    get_type_hints,
)

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
from ._type_helpers import (
    CONTEXT_ADMISSION_PROTOCOL_VERSION,
    ContextAdmissionValidationError,
    UnsupportedContextAdmissionProtocolError,
    _matches_declared_type,
    _raise_invalid,
    _validate_bounded_text,
    _validate_canonical_tuple,
    _validate_context_admission_state_metadata,
    _validate_expired_idempotency_tombstone,
    _validate_freshness_policy,
    _validate_git_revision,
    _validate_iso_date,
    _validate_non_negative,
    _validate_protocol_version,
    _validate_reason_code,
)
from ._type_results import ModelIdentity

_TYPE_REGISTRY: dict[str, type[_ContractValue]] = {}
_MAX_CLOSED_EPOCH_OCCURRENCES = 10_000
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


def _encode(value: object) -> object:
    if isinstance(value, DispatchIdentity):
        try:
            validated = DispatchIdentity(
                dispatch_id=value.dispatch_id,
                completion_marker=value.completion_marker,
                sentinel_open=value.sentinel_open,
                sentinel_close=value.sentinel_close,
                sentinel_contract=value.sentinel_contract,
            )
        except ValueError:
            _raise_invalid("invalid_dispatch_identity")
        return {"dispatch_id": validated.dispatch_id}
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
        if set(value) != {"__enum__", "value"}:
            _raise_invalid("unknown_serialized_enum")
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
        except (TypeError, ValueError):
            raise ContextAdmissionValidationError("invalid_serialized_enum") from None
    if "__tuple__" in value:
        if set(value) != {"__tuple__"}:
            _raise_invalid("invalid_serialized_tuple")
        raw = value["__tuple__"]
        if not isinstance(raw, list):
            _raise_invalid("invalid_serialized_tuple")
        return tuple(_decode(item) for item in raw)
    if "__frozenset__" in value:
        if set(value) != {"__frozenset__"}:
            _raise_invalid("invalid_serialized_frozenset")
        raw = value["__frozenset__"]
        if not isinstance(raw, list):
            _raise_invalid("invalid_serialized_frozenset")
        return frozenset(_decode(item) for item in raw)
    type_name = value.get("__type__")
    if type_name == "ModelIdentity":
        if set(value) != {
            "__type__",
            "configured_model",
            "effective_model",
            "profile_name",
        }:
            _raise_invalid("invalid_model_identity")
        configured_model = value["configured_model"]
        effective_model = value["effective_model"]
        profile_name = value["profile_name"]
        if not all(
            isinstance(item, str) for item in (configured_model, effective_model, profile_name)
        ):
            _raise_invalid("invalid_model_identity")
        return ModelIdentity(
            configured_model=configured_model,
            effective_model=effective_model,
            profile_name=profile_name,
        )
    if not isinstance(type_name, str) or type_name not in _TYPE_REGISTRY:
        _raise_invalid("unknown_serialized_contract_type")
    contract_type = _TYPE_REGISTRY[type_name]
    kwargs = {key: _decode(item) for key, item in value.items() if key != "__type__"}
    try:
        return contract_type(**kwargs)
    except TypeError:
        raise ContextAdmissionValidationError("invalid_serialized_contract") from None


class _ContractMeta(type):
    def __call__(cls, *args: Any, **kwargs: Any) -> Any:
        try:
            instance = super().__call__(*args, **kwargs)
        except ContextAdmissionValidationError:
            raise
        except (AttributeError, TypeError, ValueError):
            raise ContextAdmissionValidationError("invalid_contract_field_type") from None
        _validate_declared_field_types(instance)
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


def _validate_declared_field_types(value: _ContractValue) -> None:
    declared_types = get_type_hints(type(value))
    for declared_field in fields(value):
        declared_type = declared_types.get(declared_field.name)
        if declared_type is None or not _matches_declared_type(
            getattr(value, declared_field.name),
            declared_type,
        ):
            _raise_invalid("invalid_contract_field_type")


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
        for model_value in (
            self.model_identity.configured_model,
            self.model_identity.effective_model,
        ):
            _validate_bounded_text(
                model_value,
                "invalid_model_identity",
                maximum=128,
            )
        if self.model_identity.profile_name:
            _validate_bounded_text(
                self.model_identity.profile_name,
                "invalid_model_identity",
                maximum=64,
            )


@dataclass(frozen=True, slots=True)
class CanonicalSpanOwner(_ContractValue):
    span_id: CanonicalSpanId
    occurrence_id: AdmissionOccurrenceId


@dataclass(frozen=True, slots=True)
class CanonicalRepresentationManifest(_ContractValue):
    request_id: AdmissionRequestId
    representation_revision: RepresentationRevision
    representation_binding_id: RepresentationBindingId
    span_owners: tuple[CanonicalSpanOwner, ...]
    assembler_identity: ProducerInstanceId
    assembler_witness_id: AdmissionWitnessId

    def __post_init__(self) -> None:
        _validate_canonical_tuple(
            self.span_owners,
            "noncanonical_span_owners",
            key=lambda owner: (owner.span_id.value, owner.occurrence_id.value),
        )
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
        _validate_canonical_tuple(
            self.owned_span_ids,
            "noncanonical_owned_span_ids",
            key=lambda span_id: span_id.value,
        )
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
        _validate_canonical_tuple(
            self.occurrence_ids,
            "noncanonical_batch_occurrences",
            key=lambda occurrence_id: occurrence_id.value,
        )
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
        _validate_canonical_tuple(
            self.occurrence_revisions,
            "noncanonical_occurrence_revisions",
            key=lambda pair: (pair[0].value, pair[1].value),
        )
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
        _validate_canonical_tuple(
            self.occurrence_ids,
            "noncanonical_reservation_occurrences",
            key=lambda occurrence_id: occurrence_id.value,
        )
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
    representation_binding_id: RepresentationBindingId
    occurrence_ids: tuple[AdmissionOccurrenceId, ...]
    authority_source_id: AuthoritySourceId

    def __post_init__(self) -> None:
        _validate_non_negative(self.window_epoch_number, "invalid_window_epoch_number")
        _validate_non_negative(self.snapshot_sequence, "invalid_snapshot_sequence")
        _validate_canonical_tuple(
            self.occurrence_ids,
            "noncanonical_witness_occurrences",
            key=lambda occurrence_id: occurrence_id.value,
        )
        if len(self.occurrence_ids) != len(set(self.occurrence_ids)) or (
            not self.occurrence_ids and self.kind is not WitnessKind.EPOCH_ROLLOVER
        ):
            _raise_invalid("invalid_witness_occurrences")


@dataclass(frozen=True, slots=True)
class RepresentationBindingWitness(_ContractValue):
    counted_representation_revision: RepresentationRevision
    dispatched_representation_revision: RepresentationRevision
    final_manifest_revision: RepresentationRevision
    representation_binding_id: RepresentationBindingId
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
        if self.required_release_witness_kind not in {
            WitnessKind.NON_ADMISSION,
            WitnessKind.ROLLBACK,
        }:
            _raise_invalid("invalid_protected_release_witness_kind")


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
        _validate_reason_code(self.reason_code)
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
        _validate_canonical_tuple(
            self.accepted_witness_ids,
            "noncanonical_witness_ids",
            key=lambda witness_id: witness_id.value,
        )
        if len(self.accepted_witness_ids) != len(set(self.accepted_witness_ids)):
            _raise_invalid("duplicate_witness_id")
        if self.indeterminate_reason_code is not None:
            _validate_reason_code(
                self.indeterminate_reason_code,
                "invalid_indeterminate_reason",
            )
        if self.quarantine_reason_code is not None:
            _validate_reason_code(
                self.quarantine_reason_code,
                "invalid_quarantine_reason",
            )


@dataclass(frozen=True, slots=True)
class AdmissionBatchRecord(_ContractValue):
    batch: AdmissionBatch
    state: AdmissionState
    reservation_id: AdmissionReservationId | None
    witness_ids: tuple[AdmissionWitnessId, ...]
    committed_input_count: int
    unresolved_input_count: int

    def charged_input_count(self, reservation: AdmissionReservation | None) -> int:
        """Return the capacity charge represented by this lifecycle record."""
        if self.state in {AdmissionState.COMMITTED, AdmissionState.QUARANTINED}:
            return self.committed_input_count
        if self.state is AdmissionState.INDETERMINATE:
            return self.unresolved_input_count or (
                reservation.reserved_count if reservation is not None else 0
            )
        if self.state in {
            AdmissionState.RESERVED,
            AdmissionState.PREPARED,
            AdmissionState.HISTORY_STAGED,
            AdmissionState.REQUEST_DISPATCHED,
        }:
            return reservation.reserved_count if reservation is not None else 0
        return 0

    def __post_init__(self) -> None:
        _validate_canonical_tuple(
            self.witness_ids,
            "noncanonical_witness_ids",
            key=lambda witness_id: witness_id.value,
        )
        if len(self.witness_ids) != len(set(self.witness_ids)):
            _raise_invalid("duplicate_witness_id")
        _validate_non_negative(self.committed_input_count, "invalid_committed_count")
        _validate_non_negative(self.unresolved_input_count, "invalid_unresolved_count")
        if self.committed_input_count > 0 and self.unresolved_input_count > 0:
            _raise_invalid("committed_and_unresolved_simultaneously")
        if self.committed_input_count > 0 and self.state not in {
            AdmissionState.COMMITTED,
            AdmissionState.QUARANTINED,
        }:
            _raise_invalid("committed_count_for_nonterminal_batch")
        if self.unresolved_input_count > 0 and self.state is not AdmissionState.INDETERMINATE:
            _raise_invalid("unresolved_count_for_resolved_batch")


@dataclass(frozen=True, slots=True)
class GenerationReservationRecord(_ContractValue):
    generation_reservation_id: GenerationReservationId
    request_id: AdmissionRequestId
    batch_id: AdmissionBatchId
    representation_revision: RepresentationRevision
    occurrence_ids: tuple[AdmissionOccurrenceId, ...]
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
    authority_source_id: AuthoritySourceId | None

    def charged_output_count(self) -> int:
        """Return the generation capacity charge for this lifecycle record."""
        if self.state in {
            GenerationState.RESERVED,
            GenerationState.STREAMING,
            GenerationState.INDETERMINATE,
            GenerationState.QUARANTINED,
        }:
            return self.maximum_allowance
        return 0

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
        _validate_canonical_tuple(
            self.occurrence_ids,
            "noncanonical_generation_occurrences",
            key=lambda occurrence_id: occurrence_id.value,
        )
        if not self.occurrence_ids or len(self.occurrence_ids) != len(set(self.occurrence_ids)):
            _raise_invalid("invalid_generation_occurrences")
        _validate_canonical_tuple(
            self.witness_ids,
            "noncanonical_witness_ids",
            key=lambda witness_id: witness_id.value,
        )
        if len(self.witness_ids) != len(set(self.witness_ids)):
            _raise_invalid("duplicate_witness_id")


@dataclass(frozen=True, slots=True)
class ExpiredIdempotencyTombstone(_ContractValue):
    namespace: IdempotencyNamespace
    reservation_key: AdmissionReservationKey
    original_descriptor: ReserveRequestEvent
    expiry_witness: AdmissionWitness
    original_terminal_decision: AdmissionDecision

    def __post_init__(self) -> None:
        _validate_expired_idempotency_tombstone(self)


@dataclass(frozen=True, slots=True)
class ClosedEpochAudit(_ContractValue):
    snapshot: ContextWindowSnapshot
    terminal_occurrence_records: tuple[AdmissionOccurrenceRecord, ...]
    terminal_batch_records: tuple[AdmissionBatchRecord, ...]
    terminal_reservations: tuple[AdmissionReservation, ...]
    terminal_generation_reservations: tuple[GenerationReservationRecord, ...]
    closure_witness_id: AdmissionWitnessId
    fence_proof: EpochFenceProof | None
    processed_event_tombstones: tuple[AdmissionEventId, ...]
    retained_unresolved_count: int
    retained_generation_count: int

    def __post_init__(self) -> None:
        _validate_non_negative(self.retained_unresolved_count, "invalid_retained_charge")
        _validate_non_negative(
            self.retained_generation_count,
            "invalid_retained_generation_charge",
        )
        if len(self.terminal_occurrence_records) > _MAX_CLOSED_EPOCH_OCCURRENCES:
            _raise_invalid("closed_epoch_occurrence_limit_exceeded")
        _validate_canonical_tuple(
            self.terminal_occurrence_records,
            "noncanonical_terminal_occurrences",
            key=lambda record: record.occurrence.occurrence_id.value,
        )
        _validate_canonical_tuple(
            self.terminal_batch_records,
            "noncanonical_terminal_batches",
            key=lambda record: record.batch.batch_id.value,
        )
        _validate_canonical_tuple(
            self.terminal_reservations,
            "noncanonical_terminal_reservations",
            key=lambda reservation: reservation.reservation_id.value,
        )
        _validate_canonical_tuple(
            self.terminal_generation_reservations,
            "noncanonical_terminal_generation_reservations",
            key=lambda record: record.generation_reservation_id.value,
        )
        _validate_canonical_tuple(
            self.processed_event_tombstones,
            "noncanonical_processed_event_tombstones",
            key=lambda event_id: event_id.value,
        )
        occurrence_by_id = {
            record.occurrence.occurrence_id: record for record in self.terminal_occurrence_records
        }
        batch_by_id = {record.batch.batch_id: record for record in self.terminal_batch_records}
        reservation_by_id = {
            reservation.reservation_id: reservation for reservation in self.terminal_reservations
        }
        if (
            len(occurrence_by_id) != len(self.terminal_occurrence_records)
            or len(batch_by_id) != len(self.terminal_batch_records)
            or len(reservation_by_id) != len(self.terminal_reservations)
            or len(
                {
                    record.generation_reservation_id
                    for record in self.terminal_generation_reservations
                }
            )
            != len(self.terminal_generation_reservations)
            or len(set(self.processed_event_tombstones)) != len(self.processed_event_tombstones)
        ):
            _raise_invalid("duplicate_closed_epoch_owner")
        for batch_record in self.terminal_batch_records:
            members = tuple(
                occurrence_by_id.get(occurrence_id)
                for occurrence_id in batch_record.batch.occurrence_ids
            )
            if any(member is None for member in members):
                _raise_invalid("missing_closed_epoch_occurrence")
            if any(
                member is not None
                and (
                    member.batch_id != batch_record.batch.batch_id
                    or member.reservation_id != batch_record.reservation_id
                    or member.state is not batch_record.state
                )
                for member in members
            ):
                _raise_invalid("inconsistent_closed_epoch_link")
            if batch_record.reservation_id is not None:
                reservation = reservation_by_id.get(batch_record.reservation_id)
                if reservation is None or reservation.key.batch_id != batch_record.batch.batch_id:
                    _raise_invalid("missing-closed-epoch-reservation")
        for generation in self.terminal_generation_reservations:
            generation_batch = batch_by_id.get(generation.batch_id)
            if (
                generation_batch is None
                or generation.request_id != generation_batch.batch.request_id
                or generation.representation_revision
                != generation_batch.batch.manifest.representation_revision
                or generation.occurrence_ids != generation_batch.batch.occurrence_ids
            ):
                _raise_invalid("inconsistent_closed_epoch_generation")
        retained_input = 0
        for record in self.terminal_batch_records:
            if record.state not in {
                AdmissionState.REQUEST_DISPATCHED,
                AdmissionState.INDETERMINATE,
            }:
                continue
            charge = record.unresolved_input_count
            if charge == 0 and record.reservation_id is not None:
                reservation = reservation_by_id.get(record.reservation_id)
                if reservation is not None:
                    charge = reservation.reserved_count
            retained_input += charge
        retained_generation = sum(
            record.maximum_allowance
            for record in self.terminal_generation_reservations
            if record.state
            in {
                GenerationState.RESERVED,
                GenerationState.STREAMING,
                GenerationState.INDETERMINATE,
            }
        )
        if (
            retained_input != self.retained_unresolved_count
            or retained_generation != self.retained_generation_count
        ):
            _raise_invalid("closed_epoch_retained_charge_mismatch")


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
            (self.tested_version, "invalid_tested_version", 64),
        ):
            _validate_bounded_text(value, reason, maximum=maximum)
        _validate_git_revision(self.tested_revision)
        _validate_iso_date(self.checked_at)
        _validate_freshness_policy(self.freshness_policy)
        _validate_bounded_text(
            self.source_locator,
            "invalid_source_locator",
            maximum=256,
            locator=True,
        )


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
        _validate_reason_code(self.reason_code)
        _validate_canonical_tuple(
            self.evidence,
            "noncanonical_coverage_evidence",
            key=lambda evidence: (
                evidence.kind.value,
                evidence.source_locator,
                evidence.claim_id,
            ),
        )
        if not self.evidence:
            _raise_invalid("coverage_evidence_required")
        if len(self.evidence) != 1:
            _raise_invalid("single_coverage_evidence_required")
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

    def __post_init__(self) -> None:
        _AdmissionEventBase.__post_init__(self)
        _validate_canonical_tuple(
            self.protected_pools,
            "noncanonical_protected_pools",
            key=lambda pool: (
                pool.priority,
                pool.reserve_class.value,
                pool.capability_owner_id.value,
            ),
        )


@dataclass(frozen=True, slots=True)
class AuthorityUnavailableEvent(_AdmissionEventBase):
    reason_code: str
    authority_state: CoverageState

    def __post_init__(self) -> None:
        _AdmissionEventBase.__post_init__(self)
        _validate_reason_code(self.reason_code)
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
        _validate_canonical_tuple(
            self.input_reservations,
            "noncanonical_input_reservations",
            key=lambda reservation: reservation.reservation_id.value,
        )
        if not self.input_reservations:
            _raise_invalid("input_reservation_required")
        reservation_ids = tuple(
            reservation.reservation_id for reservation in self.input_reservations
        )
        if len(reservation_ids) != len(set(reservation_ids)):
            _raise_invalid("duplicate_reservation_id")
        for reservation in self.input_reservations:
            if reservation.occurrence_ids != self.batch.occurrence_ids:
                _raise_invalid("reservation_occurrence_mismatch")
            if self.idempotency_namespace != reservation.key.idempotency_namespace:
                _raise_invalid("reservation_namespace_mismatch")
            if (
                reservation.key.batch_id != self.batch.batch_id
                or reservation.reserve_class is not self.batch.reserve_class
                or reservation.protected_pool_owner_id != self.batch.protected_pool_owner_id
            ):
                _raise_invalid("reservation_batch_policy_mismatch")
        generation = self.generation_reservation
        if generation is not None and (
            generation.state is not GenerationState.RESERVED
            or generation.exact_terminal_usage is not None
            or generation.witness_ids
            or generation.authority_source_id is not None
        ):
            _raise_invalid("generation_reservation_not_open")
        if generation is not None and (
            generation.request_id != self.batch.request_id
            or generation.batch_id != self.batch.batch_id
            or generation.representation_revision != self.batch.manifest.representation_revision
            or generation.occurrence_ids != self.batch.occurrence_ids
            or generation.reserve_class is not self.batch.reserve_class
            or generation.protected_pool_owner_id != self.batch.protected_pool_owner_id
        ):
            _raise_invalid("generation_batch_policy_mismatch")


@dataclass(frozen=True, slots=True)
class PrepareBatchEvent(_AdmissionEventBase):
    batch_id: AdmissionBatchId
    representation_revision: RepresentationRevision
    representation_binding_id: RepresentationBindingId
    proposed_charge: int
    measurement_kind: MeasurementKind
    authority_source: AuthoritySourceId

    def __post_init__(self) -> None:
        _AdmissionEventBase.__post_init__(self)
        _validate_non_negative(self.proposed_charge, "invalid_proposed_charge")
        if self.measurement_kind in {
            MeasurementKind.HOST_ESTIMATE,
            MeasurementKind.BYTE_EMERGENCY,
        }:
            _raise_invalid("non-authoritative-measurement")


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
    final_manifest: CanonicalRepresentationManifest
    exact_input_charge: int
    measurement_kind: MeasurementKind
    authority_source: AuthoritySourceId
    representation_binding_witness: RepresentationBindingWitness

    def __post_init__(self) -> None:
        _AdmissionEventBase.__post_init__(self)
        _validate_non_negative(self.exact_input_charge, "invalid_exact_input_charge")
        if self.measurement_kind is not MeasurementKind.PROVIDER_EXACT:
            _raise_invalid("non-authoritative-measurement")


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
        _validate_reason_code(self.reason_code)


@dataclass(frozen=True, slots=True)
class ResolveIndeterminateAcceptedEvent(_AdmissionEventBase):
    batch_id: AdmissionBatchId
    witness: AdmissionWitness
    final_manifest_revision: RepresentationRevision
    final_manifest: CanonicalRepresentationManifest
    exact_charge: int
    measurement_kind: MeasurementKind
    authority_source: AuthoritySourceId
    representation_binding_witness: RepresentationBindingWitness

    def __post_init__(self) -> None:
        _AdmissionEventBase.__post_init__(self)
        _validate_non_negative(self.exact_charge, "invalid-exact-charge")
        if self.measurement_kind is not MeasurementKind.PROVIDER_EXACT:
            _raise_invalid("non-authoritative-measurement")


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
        _validate_reason_code(self.reason_code)


@dataclass(frozen=True, slots=True)
class RequestReconciliationEvent(_AdmissionEventBase):
    target_id: AdmissionBatchId | GenerationReservationId
    reason_code: str

    def __post_init__(self) -> None:
        _AdmissionEventBase.__post_init__(self)
        _validate_reason_code(self.reason_code)


@dataclass(frozen=True, slots=True)
class ExpireIdempotencyKeyEvent(_AdmissionEventBase):
    reservation_key: AdmissionReservationKey
    expiry_witness: AdmissionWitness


@dataclass(frozen=True, slots=True)
class RolloverEpochEvent(_AdmissionEventBase):
    witness: AdmissionWitness
    fence_proof: EpochFenceProof | None
    new_snapshot: ContextWindowSnapshot
    protected_pools: tuple[ProtectedPoolSpec, ...]

    def __post_init__(self) -> None:
        _AdmissionEventBase.__post_init__(self)
        _validate_canonical_tuple(
            self.protected_pools,
            "noncanonical_protected_pools",
            key=lambda pool: (
                pool.priority,
                pool.reserve_class.value,
                pool.capability_owner_id.value,
            ),
        )


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
        | AdmissionEventId
        | GenerationReservationId
        | WindowEpochId
    )

    def __post_init__(self) -> None:
        if not isinstance(self.source_event_id, AdmissionEventId):
            _raise_invalid("invalid_effect_source_event")
        if not isinstance(self.resulting_aggregate_revision, AggregateRevision):
            _raise_invalid("invalid_effect_aggregate_revision")
        if not isinstance(self.resulting_admission_sequence, AdmissionSequence):
            _raise_invalid("invalid_effect_admission_sequence")


def _validate_charge_effect(
    effect: _AdmissionEffectBase,
    *,
    target_type: type[_OpaqueString],
    charge_domain: ChargeDomain,
) -> None:
    _AdmissionEffectBase.__post_init__(effect)
    if not isinstance(effect.target_id, target_type):
        _raise_invalid("invalid_effect_target")
    if getattr(effect, "charge_domain") is not charge_domain:
        _raise_invalid("invalid_effect_charge_domain")
    reserve_class = getattr(effect, "reserve_class")
    owner = getattr(effect, "protected_pool_owner_id")
    if (reserve_class is ReserveClass.ORDINARY) != (owner is None):
        _raise_invalid("invalid_effect_protected_pool_owner")
    _validate_non_negative(getattr(effect, "count"), "invalid_effect_count")
    _validate_non_negative(
        getattr(effect, "snapshot_sequence"),
        "invalid_effect_snapshot_sequence",
    )
    witness_ids = getattr(effect, "witness_ids")
    _validate_canonical_tuple(
        witness_ids,
        "noncanonical_effect_witness_ids",
        key=lambda witness_id: witness_id.value,
    )
    if len(witness_ids) != len(set(witness_ids)):
        _raise_invalid("duplicate_effect_witness_id")


@dataclass(frozen=True, slots=True)
class ReservationRecordedEffect(_AdmissionEffectBase):
    target_id: AdmissionReservationId
    charge_domain: ChargeDomain
    reserve_class: ReserveClass
    protected_pool_owner_id: ProtectedPoolOwnerId | None
    count: int
    window_epoch_id: WindowEpochId
    snapshot_sequence: int
    witness_ids: tuple[AdmissionWitnessId, ...]

    def __post_init__(self) -> None:
        _validate_charge_effect(
            self,
            target_type=AdmissionReservationId,
            charge_domain=ChargeDomain.INPUT_CONTEXT,
        )


@dataclass(frozen=True, slots=True)
class ReservationReleasedEffect(_AdmissionEffectBase):
    target_id: AdmissionReservationId
    charge_domain: ChargeDomain
    reserve_class: ReserveClass
    protected_pool_owner_id: ProtectedPoolOwnerId | None
    count: int
    window_epoch_id: WindowEpochId
    snapshot_sequence: int
    witness_ids: tuple[AdmissionWitnessId, ...]

    def __post_init__(self) -> None:
        _validate_charge_effect(
            self,
            target_type=AdmissionReservationId,
            charge_domain=ChargeDomain.INPUT_CONTEXT,
        )


@dataclass(frozen=True, slots=True)
class OccurrenceStateChangedEffect(_AdmissionEffectBase):
    target_id: AdmissionOccurrenceId
    previous_state: AdmissionState
    next_state: AdmissionState

    def __post_init__(self) -> None:
        _AdmissionEffectBase.__post_init__(self)
        if not isinstance(self.target_id, AdmissionOccurrenceId):
            _raise_invalid("invalid_effect_target")


@dataclass(frozen=True, slots=True)
class ChargeCommittedEffect(_AdmissionEffectBase):
    target_id: AdmissionBatchId
    charge_domain: ChargeDomain
    reserve_class: ReserveClass
    protected_pool_owner_id: ProtectedPoolOwnerId | None
    count: int
    window_epoch_id: WindowEpochId
    snapshot_sequence: int
    witness_ids: tuple[AdmissionWitnessId, ...]

    def __post_init__(self) -> None:
        _validate_charge_effect(
            self,
            target_type=AdmissionBatchId,
            charge_domain=ChargeDomain.INPUT_CONTEXT,
        )


@dataclass(frozen=True, slots=True)
class GenerationReservationRecordedEffect(_AdmissionEffectBase):
    target_id: GenerationReservationId
    charge_domain: ChargeDomain
    reserve_class: ReserveClass
    protected_pool_owner_id: ProtectedPoolOwnerId | None
    count: int
    window_epoch_id: WindowEpochId
    snapshot_sequence: int
    witness_ids: tuple[AdmissionWitnessId, ...]

    def __post_init__(self) -> None:
        _validate_charge_effect(
            self,
            target_type=GenerationReservationId,
            charge_domain=ChargeDomain.OUTPUT_GENERATION,
        )


@dataclass(frozen=True, slots=True)
class GenerationReconciledEffect(_AdmissionEffectBase):
    target_id: GenerationReservationId
    charge_domain: ChargeDomain
    reserve_class: ReserveClass
    protected_pool_owner_id: ProtectedPoolOwnerId | None
    count: int
    window_epoch_id: WindowEpochId
    snapshot_sequence: int
    witness_ids: tuple[AdmissionWitnessId, ...]

    def __post_init__(self) -> None:
        _validate_charge_effect(
            self,
            target_type=GenerationReservationId,
            charge_domain=ChargeDomain.OUTPUT_GENERATION,
        )


@dataclass(frozen=True, slots=True)
class ReconciliationQueryRequestedEffect(_AdmissionEffectBase):
    target_id: AdmissionBatchId | GenerationReservationId
    reason_code: str

    def __post_init__(self) -> None:
        _AdmissionEffectBase.__post_init__(self)
        if not isinstance(self.target_id, AdmissionBatchId | GenerationReservationId):
            _raise_invalid("invalid_effect_target")
        _validate_reason_code(self.reason_code)


@dataclass(frozen=True, slots=True)
class ReconciliationEscalationEffect(_AdmissionEffectBase):
    target_id: AdmissionBatchId | GenerationReservationId
    reason_code: str

    def __post_init__(self) -> None:
        _AdmissionEffectBase.__post_init__(self)
        if not isinstance(self.target_id, AdmissionBatchId | GenerationReservationId):
            _raise_invalid("invalid_effect_target")
        _validate_reason_code(self.reason_code)


@dataclass(frozen=True, slots=True)
class ConflictRejectedEffect(_AdmissionEffectBase):
    target_id: AdmissionEventId
    reason_code: str

    def __post_init__(self) -> None:
        _AdmissionEffectBase.__post_init__(self)
        if not isinstance(self.target_id, AdmissionEventId):
            _raise_invalid("invalid_effect_target")
        _validate_reason_code(self.reason_code)


@dataclass(frozen=True, slots=True)
class IdempotencyExpiredEffect(_AdmissionEffectBase):
    target_id: AdmissionReservationId
    reservation_key: AdmissionReservationKey
    expiry_witness_id: AdmissionWitnessId

    def __post_init__(self) -> None:
        _AdmissionEffectBase.__post_init__(self)
        if not isinstance(self.target_id, AdmissionReservationId):
            _raise_invalid("invalid_effect_target")


@dataclass(frozen=True, slots=True)
class ReservationInvalidatedEffect(_AdmissionEffectBase):
    target_id: AdmissionReservationId | GenerationReservationId
    charge_domain: ChargeDomain
    reserve_class: ReserveClass
    protected_pool_owner_id: ProtectedPoolOwnerId | None
    count: int
    window_epoch_id: WindowEpochId
    snapshot_sequence: int
    witness_ids: tuple[AdmissionWitnessId, ...]

    def __post_init__(self) -> None:
        if isinstance(self.target_id, AdmissionReservationId):
            expected_domain = ChargeDomain.INPUT_CONTEXT
        elif isinstance(self.target_id, GenerationReservationId):
            expected_domain = ChargeDomain.OUTPUT_GENERATION
        else:
            _raise_invalid("invalid_effect_target")
        _validate_charge_effect(
            self, target_type=type(self.target_id), charge_domain=expected_domain
        )


@dataclass(frozen=True, slots=True)
class EpochClosedEffect(_AdmissionEffectBase):
    target_id: WindowEpochId
    fence_proof: EpochFenceProof | None
    deducted_unresolved_count: int

    def __post_init__(self) -> None:
        _AdmissionEffectBase.__post_init__(self)
        if not isinstance(self.target_id, WindowEpochId):
            _raise_invalid("invalid_effect_target")
        if self.fence_proof is not None and self.target_id != self.fence_proof.old_window_epoch_id:
            _raise_invalid("invalid_effect_target")
        _validate_non_negative(
            self.deducted_unresolved_count,
            "invalid_deducted_unresolved_count",
        )


@dataclass(frozen=True, slots=True)
class QuarantineRecordedEffect(_AdmissionEffectBase):
    target_id: AdmissionBatchId | GenerationReservationId
    reason_code: str

    def __post_init__(self) -> None:
        _AdmissionEffectBase.__post_init__(self)
        if not isinstance(self.target_id, AdmissionBatchId | GenerationReservationId):
            _raise_invalid("invalid_effect_target")
        _validate_reason_code(self.reason_code)


@dataclass(frozen=True, slots=True)
class AuthorityUnavailableEffect(_AdmissionEffectBase):
    target_id: WindowEpochId
    reason_code: str
    authority_state: CoverageState

    def __post_init__(self) -> None:
        _AdmissionEffectBase.__post_init__(self)
        if not isinstance(self.target_id, WindowEpochId):
            _raise_invalid("invalid_effect_target")
        _validate_reason_code(self.reason_code)
        if self.authority_state is CoverageState.VERIFIED:
            _raise_invalid("invalid_unavailable_authority_state")


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

    def __post_init__(self) -> None:
        if self.event_id != self.event.event_id:
            _raise_invalid("processed_event_identity_mismatch")


@dataclass(frozen=True, slots=True)
class IdempotencyRecord(_ContractValue):
    namespace: IdempotencyNamespace
    reservation_key: AdmissionReservationKey
    original_descriptor: ReserveRequestEvent
    original_reserve_decision: AdmissionDecision
    owning_event_id: AdmissionEventId
    publication_revision: AggregateRevision

    def __post_init__(self) -> None:
        input_reservations = self.original_descriptor.input_reservations
        if (
            self.namespace != self.original_descriptor.idempotency_namespace
            or len(input_reservations) != 1
            or self.reservation_key != input_reservations[0].key
            or self.owning_event_id != self.original_descriptor.event_id
        ):
            _raise_invalid("idempotency_record_identity_mismatch")


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
        _validate_canonical_tuple(
            self.processed_events,
            "noncanonical_processed_events",
            key=lambda record: (record.aggregate_revision.value, record.event_id.value),
        )
        _validate_canonical_tuple(
            self.idempotency_records,
            "noncanonical_idempotency_records",
            key=lambda record: (
                record.publication_revision.value,
                record.owning_event_id.value,
            ),
        )
        _validate_canonical_tuple(
            self.expired_idempotency_tombstones,
            "noncanonical_idempotency_tombstones",
            key=lambda tombstone: (
                tombstone.reservation_key.window_epoch_number,
                tombstone.reservation_key.batch_id.value,
            ),
        )
        _validate_canonical_tuple(
            self.closed_epochs,
            "noncanonical_closed_epochs",
            key=lambda audit: audit.snapshot.window_epoch_number,
        )
        _validate_context_admission_state_metadata(
            self.aggregate_revision,
            self.admission_sequence,
            self.processed_events,
            self.idempotency_records,
            self.expired_idempotency_tombstones,
            self.closed_epochs,
        )


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
        _validate_canonical_tuple(
            self.protected_pools,
            "noncanonical_protected_pools",
            key=lambda pool: (
                pool.priority,
                pool.reserve_class.value,
                pool.capability_owner_id.value,
            ),
        )
        _validate_canonical_tuple(
            self.occurrence_records,
            "noncanonical_occurrence_records",
            key=lambda record: record.occurrence.occurrence_id.value,
        )
        _validate_canonical_tuple(
            self.batch_records,
            "noncanonical_batch_records",
            key=lambda record: record.batch.batch_id.value,
        )
        _validate_canonical_tuple(
            self.reservations,
            "noncanonical_reservations",
            key=lambda reservation: reservation.reservation_id.value,
        )
        _validate_canonical_tuple(
            self.generation_reservations,
            "noncanonical_generation_reservations",
            key=lambda record: record.generation_reservation_id.value,
        )
        _validate_canonical_tuple(
            self.processed_events,
            "noncanonical_processed_events",
            key=lambda record: (record.aggregate_revision.value, record.event_id.value),
        )
        _validate_canonical_tuple(
            self.idempotency_records,
            "noncanonical_idempotency_records",
            key=lambda record: (
                record.publication_revision.value,
                record.owning_event_id.value,
            ),
        )
        _validate_canonical_tuple(
            self.expired_idempotency_tombstones,
            "noncanonical_idempotency_tombstones",
            key=lambda tombstone: (
                tombstone.reservation_key.window_epoch_number,
                tombstone.reservation_key.batch_id.value,
            ),
        )
        _validate_canonical_tuple(
            self.closed_epochs,
            "noncanonical_closed_epochs",
            key=lambda audit: audit.snapshot.window_epoch_number,
        )
        _validate_context_admission_state_metadata(
            self.aggregate_revision,
            self.admission_sequence,
            self.processed_events,
            self.idempotency_records,
            self.expired_idempotency_tombstones,
            self.closed_epochs,
        )
        pools = tuple(
            (pool.reserve_class, pool.capability_owner_id) for pool in self.protected_pools
        )
        if len(pools) != len(set(pools)):
            _raise_invalid("duplicate_protected_pool")
        if sum(pool.injected_count for pool in self.protected_pools) > (
            self.snapshot.remaining_count
        ):
            _raise_invalid("protected_pool_capacity_exceeded")
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
        pools_by_key = {
            (pool.reserve_class, pool.capability_owner_id): pool for pool in self.protected_pools
        }
        reservations_by_id = {
            reservation.reservation_id: reservation for reservation in self.reservations
        }
        batch_records_by_id = {record.batch.batch_id: record for record in self.batch_records}
        occurrence_records_by_id = {
            record.occurrence.occurrence_id: record for record in self.occurrence_records
        }
        for occurrence_record in self.occurrence_records:
            if occurrence_record.batch_id is None:
                if (
                    occurrence_record.reservation_id is not None
                    or occurrence_record.state is not AdmissionState.PROPOSED
                ):
                    _raise_invalid("orphan_occurrence_link")
                continue
            linked_batch = batch_records_by_id.get(occurrence_record.batch_id)
            if (
                linked_batch is None
                or occurrence_record.occurrence.occurrence_id
                not in linked_batch.batch.occurrence_ids
                or occurrence_record.reservation_id != linked_batch.reservation_id
                or occurrence_record.state is not linked_batch.state
            ):
                _raise_invalid("inconsistent_occurrence_link")
        for batch_record in self.batch_records:
            member_records = tuple(
                occurrence_records_by_id.get(occurrence_id)
                for occurrence_id in batch_record.batch.occurrence_ids
            )
            if any(record is None for record in member_records):
                _raise_invalid("missing_batch_occurrence")
            concrete_members = tuple(record for record in member_records if record is not None)
            if any(
                record.batch_id != batch_record.batch.batch_id
                or record.reservation_id != batch_record.reservation_id
                or record.state is not batch_record.state
                for record in concrete_members
            ):
                _raise_invalid("inconsistent_batch_occurrence_link")
            owned_pairs = tuple(
                (span_id, record.occurrence.occurrence_id)
                for record in concrete_members
                for span_id in record.occurrence.owned_span_ids
            )
            owned_span_ids = tuple(span_id for span_id, _ in owned_pairs)
            manifest_pairs = tuple(
                (owner.span_id, owner.occurrence_id)
                for owner in batch_record.batch.manifest.span_owners
            )
            if (
                len(owned_span_ids) != len(set(owned_span_ids))
                or set(owned_pairs) != set(manifest_pairs)
                or len(owned_pairs) != len(manifest_pairs)
            ):
                _raise_invalid("inconsistent-span-ownership")
        for reservation in self.reservations:
            matching_batch = batch_records_by_id.get(reservation.key.batch_id)
            if matching_batch is None:
                _raise_invalid("orphan_reservation")
            if (
                matching_batch.reservation_id != reservation.reservation_id
                or reservation.occurrence_ids != matching_batch.batch.occurrence_ids
                or reservation.reserve_class is not matching_batch.batch.reserve_class
                or reservation.protected_pool_owner_id
                != matching_batch.batch.protected_pool_owner_id
            ):
                _raise_invalid("reservation_batch_policy_mismatch")
            owner = reservation.protected_pool_owner_id
            if (
                owner is not None
                and (
                    reservation.reserve_class,
                    owner,
                )
                not in pools_by_key
            ):
                _raise_invalid("orphan_protected_charge_owner")
        protected_charges: dict[tuple[ReserveClass, ProtectedPoolOwnerId], int] = {}
        for record in self.batch_records:
            owner = record.batch.protected_pool_owner_id
            matched_reservation = (
                reservations_by_id.get(record.reservation_id)
                if record.reservation_id is not None
                else None
            )
            if (
                record.state
                in {
                    AdmissionState.RESERVED,
                    AdmissionState.PREPARED,
                    AdmissionState.HISTORY_STAGED,
                    AdmissionState.REQUEST_DISPATCHED,
                    AdmissionState.INDETERMINATE,
                }
                and matched_reservation is None
            ):
                _raise_invalid("missing_active_batch_reservation")
            if matched_reservation is not None and (
                matched_reservation.key.batch_id != record.batch.batch_id
                or matched_reservation.reserve_class is not record.batch.reserve_class
                or matched_reservation.protected_pool_owner_id != owner
            ):
                _raise_invalid("reservation_batch_policy_mismatch")
            if record.batch.reserve_class is not ReserveClass.ORDINARY:
                if owner is None:
                    _raise_invalid("missing_protected_pool_owner")
                key = (record.batch.reserve_class, owner)
                if key not in pools_by_key:
                    _raise_invalid("orphan_protected_charge_owner")
                if record.state is AdmissionState.INDETERMINATE:
                    charge = record.unresolved_input_count
                    if charge == 0 and matched_reservation is not None:
                        charge = matched_reservation.reserved_count
                elif record.state in {
                    AdmissionState.RESERVED,
                    AdmissionState.PREPARED,
                    AdmissionState.HISTORY_STAGED,
                    AdmissionState.REQUEST_DISPATCHED,
                }:
                    charge = (
                        matched_reservation.reserved_count
                        if matched_reservation is not None
                        else 0
                    )
                else:
                    # Committed/quarantined facts may exceed their reservation after
                    # an authoritative acceptance.  They remain charged by _capacity,
                    # but are no longer an outstanding allocation against the pool.
                    charge = 0
                protected_charges[key] = protected_charges.get(key, 0) + charge
        for generation_record in self.generation_reservations:
            matching_batch = batch_records_by_id.get(generation_record.batch_id)
            if (
                matching_batch is None
                or generation_record.request_id != matching_batch.batch.request_id
                or generation_record.representation_revision
                != matching_batch.batch.manifest.representation_revision
                or generation_record.occurrence_ids != matching_batch.batch.occurrence_ids
                or generation_record.reserve_class is not matching_batch.batch.reserve_class
                or generation_record.protected_pool_owner_id
                != matching_batch.batch.protected_pool_owner_id
                or generation_record.window_epoch_id != self.snapshot.window_epoch_id
                or generation_record.window_epoch_number != self.snapshot.window_epoch_number
                or generation_record.snapshot_sequence != self.snapshot.snapshot_sequence
            ):
                _raise_invalid("inconsistent_generation_link")
            owner = generation_record.protected_pool_owner_id
            if generation_record.reserve_class is not ReserveClass.ORDINARY:
                if owner is None:
                    _raise_invalid("missing_protected_pool_owner")
                key = (generation_record.reserve_class, owner)
                if key not in pools_by_key:
                    _raise_invalid("orphan_protected_charge_owner")
                if generation_record.state in {
                    GenerationState.RESERVED,
                    GenerationState.STREAMING,
                    GenerationState.INDETERMINATE,
                }:
                    protected_charges[key] = (
                        protected_charges.get(key, 0) + generation_record.maximum_allowance
                    )
        if any(
            charged > pools_by_key[key].injected_count
            for key, charged in protected_charges.items()
        ):
            _raise_invalid("protected_pool_overallocated")
        global_allocated = sum(
            record.charged_input_count(
                reservations_by_id.get(record.reservation_id)
                if record.reservation_id is not None
                else None
            )
            for record in self.batch_records
            if record.state
            in {
                AdmissionState.RESERVED,
                AdmissionState.PREPARED,
                AdmissionState.HISTORY_STAGED,
                AdmissionState.REQUEST_DISPATCHED,
                AdmissionState.INDETERMINATE,
            }
        ) + sum(
            generation.charged_output_count()
            for generation in self.generation_reservations
            if generation.state
            in {
                GenerationState.RESERVED,
                GenerationState.STREAMING,
                GenerationState.INDETERMINATE,
            }
        )
        if global_allocated > self.snapshot.remaining_count:
            _raise_invalid("context_capacity_overallocated")


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
        revision = "ac8f653a00d24b6be50ef285958cfb0e1b7a351b"
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
        reason_code="authoritative-watermark-unavailable",
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
    "RepresentationBindingId",
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
