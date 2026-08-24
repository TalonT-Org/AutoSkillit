"""Closed context-admission publication-effect contract."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TypeAlias

from ._type_context_admission_base import _ContractValue
from ._type_context_admission_identities import (
    AdmissionBatchId,
    AdmissionEventId,
    AdmissionOccurrenceId,
    AdmissionReservationId,
    AdmissionSequence,
    AdmissionWitnessId,
    AggregateRevision,
    GenerationReservationId,
    ProtectedPoolOwnerId,
    WindowEpochId,
    _OpaqueString,
)
from ._type_context_admission_records import AdmissionReservationKey, EpochFenceProof
from ._type_enums import AdmissionState, ChargeDomain, CoverageState, ReserveClass
from ._type_helpers import (
    _raise_invalid,
    _validate_canonical_tuple,
    _validate_non_negative,
    _validate_reason_code,
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
