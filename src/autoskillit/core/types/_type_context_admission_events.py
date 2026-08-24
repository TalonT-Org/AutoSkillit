"""Closed context-admission event contract."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TypeAlias

from ._type_context_admission_base import _ContractValue
from ._type_context_admission_identities import (
    AdmissionBatchId,
    AdmissionEventId,
    AggregateRevision,
    AuthoritySourceId,
    GenerationReservationId,
    IdempotencyNamespace,
    RepresentationBindingId,
    RepresentationRevision,
)
from ._type_context_admission_records import (
    AdmissionBatch,
    AdmissionOccurrence,
    AdmissionReservation,
    AdmissionReservationKey,
    AdmissionWitness,
    CanonicalRepresentationManifest,
    ContextWindowSnapshot,
    EpochFenceProof,
    GenerationReservationRecord,
    ProtectedPoolSpec,
    RepresentationBindingWitness,
)
from ._type_enums import CoverageState, GenerationState, MeasurementKind
from ._type_helpers import (
    _raise_invalid,
    _validate_canonical_tuple,
    _validate_non_negative,
    _validate_protocol_version,
    _validate_reason_code,
)


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
