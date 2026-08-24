"""Context-admission aggregate state, transition, and replay values."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TypeAlias

from ._type_context_admission_base import _ContractValue
from ._type_context_admission_effects import AdmissionEffect
from ._type_context_admission_events import ContextAdmissionEvent, ReserveRequestEvent
from ._type_context_admission_identities import (
    AdmissionEventId,
    AdmissionSequence,
    AggregateRevision,
    IdempotencyNamespace,
    ProtectedPoolOwnerId,
)
from ._type_context_admission_records import (
    AdmissionBatchRecord,
    AdmissionDecision,
    AdmissionOccurrenceRecord,
    AdmissionReservation,
    AdmissionReservationKey,
    AdmissionWitness,
    ClosedEpochAudit,
    ContextWindowSnapshot,
    GenerationReservationRecord,
    ProtectedPoolSpec,
)
from ._type_enums import AdmissionState, GenerationState, ReserveClass
from ._type_helpers import (
    _raise_invalid,
    _validate_canonical_tuple,
    _validate_context_admission_state_metadata,
    _validate_expired_idempotency_tombstone,
    _validate_protocol_version,
)


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
