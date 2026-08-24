"""Context-admission snapshots, manifests, and lifecycle records."""

from __future__ import annotations

from dataclasses import dataclass

from ._type_context_admission_base import _ContractValue
from ._type_context_admission_identities import (
    AdmissionBatchId,
    AdmissionEventId,
    AdmissionOccurrenceId,
    AdmissionRequestId,
    AdmissionReservationId,
    AdmissionWitnessId,
    AuthoritySourceId,
    CanonicalSpanId,
    ContextLineage,
    GenerationReservationId,
    IdempotencyNamespace,
    ModelItemId,
    ProducerInstanceId,
    ProtectedPoolOwnerId,
    RepresentationBindingId,
    RepresentationRevision,
    TokenizerIdentity,
    WindowEpochId,
)
from ._type_enums import (
    AdmissionDecisionKind,
    AdmissionState,
    GenerationState,
    ProducerSurface,
    ReserveClass,
    WitnessKind,
)
from ._type_helpers import (
    _raise_invalid,
    _validate_bounded_text,
    _validate_canonical_tuple,
    _validate_non_negative,
    _validate_protocol_version,
    _validate_reason_code,
)
from ._type_results import ModelIdentity

_MAX_CLOSED_EPOCH_OCCURRENCES = 10_000


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

    def reservation_for(self, record: AdmissionBatchRecord) -> AdmissionReservation | None:
        if record.reservation_id is None:
            return None
        return next(
            (
                reservation
                for reservation in self.terminal_reservations
                if reservation.reservation_id == record.reservation_id
            ),
            None,
        )

    def retained_input_count(self, records: tuple[AdmissionBatchRecord, ...]) -> int:
        return sum(
            record.charged_input_count(self.reservation_for(record))
            for record in records
            if record.state
            in {
                AdmissionState.REQUEST_DISPATCHED,
                AdmissionState.INDETERMINATE,
            }
        )

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
        retained_input = self.retained_input_count(self.terminal_batch_records)
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
