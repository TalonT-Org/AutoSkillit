"""Bounded model-based checks for cumulative context-admission accounting."""

from __future__ import annotations

from dataclasses import replace
from typing import Any

import pytest
from hypothesis import settings
from hypothesis import strategies as st
from hypothesis.stateful import RuleBasedStateMachine, invariant, precondition, rule

from autoskillit.core import (
    CONTEXT_ADMISSION_PROTOCOL_VERSION,
    AcceptInputEvent,
    ActiveContextAdmissionState,
    AdmissionAttemptId,
    AdmissionBatch,
    AdmissionBatchId,
    AdmissionDecisionKind,
    AdmissionEventId,
    AdmissionOccurrence,
    AdmissionOccurrenceId,
    AdmissionRequestId,
    AdmissionReservation,
    AdmissionReservationId,
    AdmissionReservationKey,
    AdmissionSequence,
    AdmissionState,
    AdmissionWitness,
    AdmissionWitnessId,
    AgentInstanceId,
    AggregateRevision,
    AuthoritySourceId,
    CanonicalRepresentationManifest,
    CanonicalSpanId,
    CanonicalSpanOwner,
    ChargeCommittedEffect,
    ContextLineage,
    ContextSessionId,
    ContextThreadId,
    ContextWindowSnapshot,
    DeliveryOccurrenceId,
    DispatchRequestEvent,
    EpochClosedEffect,
    EpochFenceProof,
    ExpireIdempotencyKeyEvent,
    ForkOccurrenceId,
    GenerationReconciledEffect,
    GenerationReservationId,
    GenerationReservationRecord,
    GenerationReservationRecordedEffect,
    GenerationState,
    IdempotencyExpiredEffect,
    IdempotencyNamespace,
    MarkIndeterminateEvent,
    MeasurementKind,
    ModelIdentity,
    ModelItemId,
    OccurrenceStateChangedEffect,
    OpenEpochEvent,
    PrepareBatchEvent,
    ProducerInstanceId,
    ProducerSurface,
    ProposeOccurrenceEvent,
    ProtectedPoolOwnerId,
    ProtectedPoolSpec,
    QuarantineRecordedEffect,
    ReconcileGenerationEvent,
    ReconciliationEscalationEffect,
    ReconciliationQueryRequestedEffect,
    ReleaseNonAdmissionEvent,
    RepresentationBindingId,
    RepresentationBindingWitness,
    RepresentationRevision,
    RequestReconciliationEvent,
    ReservationInvalidatedEffect,
    ReservationRecordedEffect,
    ReservationReleasedEffect,
    ReserveClass,
    ReserveRequestEvent,
    ResolveIndeterminateNonAdmissionEvent,
    ResolveIndeterminateRollbackEvent,
    RollbackAdmissionEvent,
    RolloverEpochEvent,
    StageHistoryEvent,
    StartGenerationEvent,
    TokenizerIdentity,
    ToolCallId,
    TurnId,
    UninitializedContextAdmissionState,
    WindowEpochId,
    WitnessKind,
    reduce_context_admission,
    replay_context_admission,
)

pytestmark = [pytest.mark.layer("core"), pytest.mark.small]


_SLOT = st.integers(min_value=0, max_value=1)
_RESERVE_CLASS = st.sampled_from(
    (ReserveClass.ORDINARY, ReserveClass.SYNTHESIS, ReserveClass.FINAL_RESPONSE)
)


def _namespace(operation_kind: str) -> IdempotencyNamespace:
    return IdempotencyNamespace(caller_scope="state-machine", operation_kind=operation_kind)


def _uninitialized() -> UninitializedContextAdmissionState:
    return UninitializedContextAdmissionState(
        protocol_version=CONTEXT_ADMISSION_PROTOCOL_VERSION,
        aggregate_revision=AggregateRevision(0),
        admission_sequence=AdmissionSequence(0),
        processed_events=(),
        idempotency_records=(),
        expired_idempotency_tombstones=(),
        closed_epochs=(),
    )


def _snapshot() -> ContextWindowSnapshot:
    return ContextWindowSnapshot(
        protocol_version=CONTEXT_ADMISSION_PROTOCOL_VERSION,
        window_epoch_id=WindowEpochId("epoch-state-machine"),
        window_epoch_number=1,
        model_identity=ModelIdentity.anthropic("claude-state-machine"),
        tokenizer_identity=TokenizerIdentity("tokenizer-state-machine"),
        snapshot_sequence=1,
        active_count=50,
        hard_limit=100,
        remaining_count=50,
    )


def _pool_specs() -> tuple[ProtectedPoolSpec, ...]:
    return (
        ProtectedPoolSpec(
            reserve_class=ReserveClass.SYNTHESIS,
            capability_owner_id=ProtectedPoolOwnerId("synthesis-owner"),
            injected_count=12,
            priority=10,
            required_release_witness_kind=WitnessKind.NON_ADMISSION,
        ),
        ProtectedPoolSpec(
            reserve_class=ReserveClass.FINAL_RESPONSE,
            capability_owner_id=ProtectedPoolOwnerId("final-owner"),
            injected_count=12,
            priority=20,
            required_release_witness_kind=WitnessKind.NON_ADMISSION,
        ),
    )


def _owner(reserve_class: ReserveClass) -> ProtectedPoolOwnerId | None:
    if reserve_class is ReserveClass.SYNTHESIS:
        return ProtectedPoolOwnerId("synthesis-owner")
    if reserve_class is ReserveClass.FINAL_RESPONSE:
        return ProtectedPoolOwnerId("final-owner")
    return None


def _occurrence(
    slot: int,
    reserve_class: ReserveClass,
    *,
    window_epoch_id: WindowEpochId,
    window_epoch_number: int,
) -> AdmissionOccurrence:
    name = f"occurrence-{slot}"
    surface = (
        ProducerSurface.PARENT_VISIBLE_CHILD_DELIVERY
        if slot == 1
        else ProducerSurface.TOOL_RESULT_ENVELOPE
    )
    return AdmissionOccurrence(
        occurrence_id=AdmissionOccurrenceId(name),
        lineage=ContextLineage(
            root_session_id=ContextSessionId("session-root"),
            current_session_id=ContextSessionId("session-child" if slot == 1 else "session-root"),
            root_agent_id=AgentInstanceId("agent-root"),
            current_agent_id=AgentInstanceId("agent-child" if slot == 1 else "agent-root"),
            parent_agent_id=AgentInstanceId("agent-root") if slot == 1 else None,
            root_thread_id=ContextThreadId("thread-root"),
            current_thread_id=ContextThreadId("thread-child" if slot == 1 else "thread-root"),
            parent_thread_id=ContextThreadId("thread-root") if slot == 1 else None,
            fork_occurrence_id=(ForkOccurrenceId("fork-state-machine") if slot == 1 else None),
            turn_id=TurnId(f"turn-{slot}"),
            producer_surface=surface,
            producer_instance_id=ProducerInstanceId(f"producer-{slot}"),
            tool_call_id=ToolCallId(f"tool-{slot}"),
            model_item_id=ModelItemId(f"item-{slot}"),
            dispatch_identity=None,
            attempt_id=AdmissionAttemptId(f"attempt-{slot}"),
            delivery_occurrence_id=(
                DeliveryOccurrenceId("delivery-state-machine") if slot == 1 else None
            ),
            window_epoch_id=window_epoch_id,
            window_epoch_number=window_epoch_number,
        ),
        reserve_class=reserve_class,
        producer_surface=surface,
        predicted_authoritative_maximum=15,
        representation_revision=RepresentationRevision(f"revision-{slot}"),
        owned_span_ids=(CanonicalSpanId(f"span-{slot}"),),
    )


def _batch(occurrence: AdmissionOccurrence) -> AdmissionBatch:
    slot = occurrence.occurrence_id.value.rsplit("-", 1)[-1]
    request_id = AdmissionRequestId(f"request-{slot}")
    manifest = CanonicalRepresentationManifest(
        request_id=request_id,
        representation_revision=occurrence.representation_revision,
        representation_binding_id=RepresentationBindingId(f"binding-{slot}"),
        span_owners=tuple(
            CanonicalSpanOwner(span_id=span_id, occurrence_id=occurrence.occurrence_id)
            for span_id in occurrence.owned_span_ids
        ),
        assembler_identity=ProducerInstanceId(f"assembler-{slot}"),
        assembler_witness_id=AdmissionWitnessId(f"assembler-witness-{slot}"),
    )
    return AdmissionBatch(
        batch_id=AdmissionBatchId(f"batch-{slot}"),
        request_id=request_id,
        occurrence_ids=(occurrence.occurrence_id,),
        reserve_class=occurrence.reserve_class,
        protected_pool_owner_id=_owner(occurrence.reserve_class),
        manifest=manifest,
    )


def _multi_batch(occurrences: tuple[AdmissionOccurrence, ...]) -> AdmissionBatch:
    occurrences = tuple(sorted(occurrences, key=lambda occurrence: occurrence.occurrence_id.value))
    slots = tuple(occurrence.occurrence_id.value.rsplit("-", 1)[-1] for occurrence in occurrences)
    request_id = AdmissionRequestId("request-multi-" + "-".join(slots))
    reserve_class = occurrences[0].reserve_class
    if any(occurrence.reserve_class is not reserve_class for occurrence in occurrences):
        msg = "multi-member batch must share one reserve class"
        raise ValueError(msg)
    manifest = CanonicalRepresentationManifest(
        request_id=request_id,
        representation_revision=occurrences[0].representation_revision,
        representation_binding_id=RepresentationBindingId("binding-multi-" + "-".join(slots)),
        span_owners=tuple(
            CanonicalSpanOwner(span_id=span_id, occurrence_id=occurrence.occurrence_id)
            for occurrence in occurrences
            for span_id in occurrence.owned_span_ids
        ),
        assembler_identity=ProducerInstanceId("assembler-multi-" + "-".join(slots)),
        assembler_witness_id=AdmissionWitnessId("assembler-witness-multi-" + "-".join(slots)),
    )
    return AdmissionBatch(
        batch_id=AdmissionBatchId("batch-multi-" + "-".join(slots)),
        request_id=request_id,
        occurrence_ids=tuple(occurrence.occurrence_id for occurrence in occurrences),
        reserve_class=reserve_class,
        protected_pool_owner_id=_owner(reserve_class),
        manifest=manifest,
    )


def _reservation(
    occurrence: AdmissionOccurrence, batch: AdmissionBatch, count: int
) -> AdmissionReservation:
    window_epoch_id = occurrence.lineage.window_epoch_id
    window_epoch_number = occurrence.lineage.window_epoch_number
    key = AdmissionReservationKey(
        idempotency_namespace=_namespace("reserve-request"),
        protocol_version=CONTEXT_ADMISSION_PROTOCOL_VERSION,
        window_epoch_id=window_epoch_id,
        window_epoch_number=window_epoch_number,
        batch_id=batch.batch_id,
        reserve_class=occurrence.reserve_class,
        protected_pool_owner_id=_owner(occurrence.reserve_class),
        occurrence_revisions=((occurrence.occurrence_id, occurrence.representation_revision),),
    )
    return AdmissionReservation(
        reservation_id=AdmissionReservationId(f"reservation-{occurrence.occurrence_id.value}"),
        key=key,
        window_epoch_id=window_epoch_id,
        window_epoch_number=window_epoch_number,
        snapshot_sequence=1,
        reserve_class=occurrence.reserve_class,
        protected_pool_owner_id=_owner(occurrence.reserve_class),
        occurrence_ids=(occurrence.occurrence_id,),
        reserved_count=count,
    )


def _reservation_for_batch(
    batch: AdmissionBatch,
    occurrence: AdmissionOccurrence,
    count: int,
) -> AdmissionReservation:
    occurrence_ids = batch.occurrence_ids
    slot = occurrence_ids[0].value.rsplit("-", 1)[-1]
    window_epoch_id = occurrence.lineage.window_epoch_id
    window_epoch_number = occurrence.lineage.window_epoch_number
    key = AdmissionReservationKey(
        idempotency_namespace=_namespace("reserve-multi"),
        protocol_version=CONTEXT_ADMISSION_PROTOCOL_VERSION,
        window_epoch_id=window_epoch_id,
        window_epoch_number=window_epoch_number,
        batch_id=batch.batch_id,
        reserve_class=batch.reserve_class,
        protected_pool_owner_id=batch.protected_pool_owner_id,
        occurrence_revisions=tuple(
            (occurrence_id, batch.manifest.representation_revision)
            for occurrence_id in occurrence_ids
        ),
    )
    return AdmissionReservation(
        reservation_id=AdmissionReservationId(f"reservation-multi-{slot}"),
        key=key,
        window_epoch_id=window_epoch_id,
        window_epoch_number=window_epoch_number,
        snapshot_sequence=1,
        reserve_class=batch.reserve_class,
        protected_pool_owner_id=batch.protected_pool_owner_id,
        occurrence_ids=occurrence_ids,
        reserved_count=count,
    )


def _generation(
    occurrence: AdmissionOccurrence, batch: AdmissionBatch, count: int
) -> GenerationReservationRecord:
    return GenerationReservationRecord(
        generation_reservation_id=GenerationReservationId(
            f"generation-{occurrence.occurrence_id.value}"
        ),
        request_id=batch.request_id,
        batch_id=batch.batch_id,
        representation_revision=batch.manifest.representation_revision,
        occurrence_ids=batch.occurrence_ids,
        response_id=ModelItemId(f"response-{occurrence.occurrence_id.value}"),
        window_epoch_id=occurrence.lineage.window_epoch_id,
        window_epoch_number=occurrence.lineage.window_epoch_number,
        snapshot_sequence=1,
        reserve_class=occurrence.reserve_class,
        protected_pool_owner_id=_owner(occurrence.reserve_class),
        maximum_allowance=count,
        state=GenerationState.RESERVED,
        exact_terminal_usage=None,
        witness_ids=(),
        authority_source_id=None,
    )


def _witness(
    batch: AdmissionBatch,
    occurrence: AdmissionOccurrence,
    kind: WitnessKind,
    *,
    occurrence_ids: tuple[AdmissionOccurrenceId, ...] | None = None,
) -> AdmissionWitness:
    return AdmissionWitness(
        witness_id=AdmissionWitnessId(f"{kind.value}-witness-{batch.batch_id.value}"),
        kind=kind,
        window_epoch_id=occurrence.lineage.window_epoch_id,
        window_epoch_number=occurrence.lineage.window_epoch_number,
        snapshot_sequence=1,
        request_id=batch.request_id,
        batch_id=batch.batch_id,
        representation_revision=batch.manifest.representation_revision,
        representation_binding_id=batch.manifest.representation_binding_id,
        occurrence_ids=occurrence_ids or batch.occurrence_ids,
        authority_source_id=AuthoritySourceId("authority-state-machine"),
    )


def _binding(batch: AdmissionBatch) -> RepresentationBindingWitness:
    bound_revision = batch.manifest.representation_revision
    return RepresentationBindingWitness(
        counted_representation_revision=bound_revision,
        dispatched_representation_revision=bound_revision,
        final_manifest_revision=bound_revision,
        representation_binding_id=batch.manifest.representation_binding_id,
        request_id=batch.request_id,
        batch_id=batch.batch_id,
        authority_source_id=AuthoritySourceId("authority-state-machine"),
    )


class ContextAdmissionStateMachine(RuleBasedStateMachine):
    """Compare reducer transitions with a deliberately small accounting oracle."""

    def __init__(self) -> None:
        super().__init__()
        initial = _uninitialized()
        open_event = OpenEpochEvent(
            event_id=AdmissionEventId("open-state-machine"),
            protocol_version=CONTEXT_ADMISSION_PROTOCOL_VERSION,
            idempotency_namespace=_namespace("open-epoch"),
            expected_aggregate_revision=initial.aggregate_revision,
            snapshot=_snapshot(),
            protected_pools=_pool_specs(),
        )
        opened = reduce_context_admission(initial, open_event)
        assert opened.decision.kind is AdmissionDecisionKind.WOULD_ADMIT
        assert isinstance(opened.next_state, ActiveContextAdmissionState)
        self.state = opened.next_state
        self.occurrences: dict[int, AdmissionOccurrence] = {}
        self.charges: dict[int, tuple[ReserveClass, int, int]] = {}
        self.latest_replayable_event: Any | None = open_event
        self.latest_propose_event: ProposeOccurrenceEvent | None = None
        self.events: list[Any] = [open_event]
        self.published_effects: list[tuple[object, ...]] = [opened.effects]
        self.event_sequence = 0
        self.last_revision = self.state.aggregate_revision.value
        self.last_admission_sequence = self.state.admission_sequence.value
        self.closed_audit_count = len(self.state.closed_epochs)
        self.last_rollover_retention: int | None = None

    def _find_batch(self, batch_id: AdmissionBatchId) -> Any:
        return next(
            (record for record in self.state.batch_records if record.batch.batch_id == batch_id),
            None,
        )

    def _fields(self, operation_kind: str) -> dict[str, object]:
        self.event_sequence += 1
        return {
            "event_id": AdmissionEventId(f"event-{self.event_sequence}"),
            "protocol_version": CONTEXT_ADMISSION_PROTOCOL_VERSION,
            "idempotency_namespace": _namespace(operation_kind),
            "expected_aggregate_revision": self.state.aggregate_revision,
        }

    def _accept_publication(self, transition: Any, event: object) -> None:
        assert isinstance(transition.next_state, ActiveContextAdmissionState)
        assert (
            transition.next_state.aggregate_revision.value >= self.state.aggregate_revision.value
        )
        assert (
            transition.next_state.admission_sequence.value >= self.state.admission_sequence.value
        )
        expected_effect_types = self._expected_effect_types(transition, event)
        assert tuple(type(effect) for effect in transition.effects) == expected_effect_types
        for effect in transition.effects:
            assert effect.source_event_id == getattr(event, "event_id")
            assert effect.resulting_aggregate_revision == transition.next_state.aggregate_revision
            assert effect.resulting_admission_sequence == transition.next_state.admission_sequence
        self.state = transition.next_state
        self.last_revision = self.state.aggregate_revision.value
        self.last_admission_sequence = self.state.admission_sequence.value
        event_id = getattr(event, "event_id")
        if any(record.event_id == event_id for record in self.state.processed_events):
            self.latest_replayable_event = event
        self.events.append(event)
        self.published_effects.append(transition.effects)

    def _expected_effect_types(
        self,
        transition: Any,
        event: object,
    ) -> tuple[type[object], ...]:
        if transition.decision.kind not in {
            AdmissionDecisionKind.WOULD_ADMIT,
            AdmissionDecisionKind.QUARANTINED,
        }:
            return ()
        if isinstance(event, ProposeOccurrenceEvent | StartGenerationEvent):
            return ()
        if isinstance(event, ReserveRequestEvent):
            effect_types: tuple[type[object], ...] = (
                ReservationRecordedEffect,
                *(OccurrenceStateChangedEffect for _ in event.batch.occurrence_ids),
            )
            if (
                event.generation_reservation is not None
                and event.generation_reservation.maximum_allowance > 0
            ):
                effect_types += (GenerationReservationRecordedEffect,)
            return effect_types
        if isinstance(
            event,
            PrepareBatchEvent | StageHistoryEvent | DispatchRequestEvent | MarkIndeterminateEvent,
        ):
            record = self._find_batch(event.batch_id)
            assert record is not None
            return tuple(OccurrenceStateChangedEffect for _ in record.batch.occurrence_ids)
        if isinstance(event, AcceptInputEvent):
            record = self._find_batch(event.batch_id)
            assert record is not None
            effect_types = (
                ChargeCommittedEffect,
                *(OccurrenceStateChangedEffect for _ in record.batch.occurrence_ids),
            )
            if transition.decision.kind is AdmissionDecisionKind.QUARANTINED:
                effect_types += (QuarantineRecordedEffect,)
            return effect_types
        if isinstance(
            event,
            ReleaseNonAdmissionEvent
            | RollbackAdmissionEvent
            | ResolveIndeterminateNonAdmissionEvent
            | ResolveIndeterminateRollbackEvent,
        ):
            record = self._find_batch(event.batch_id)
            assert record is not None
            generation_count = sum(
                generation.batch_id == record.batch.batch_id
                and generation.state
                in {
                    GenerationState.RESERVED,
                    GenerationState.STREAMING,
                    GenerationState.INDETERMINATE,
                }
                for generation in self.state.generation_reservations
            )
            return (
                ReservationReleasedEffect,
                *(OccurrenceStateChangedEffect for _ in record.batch.occurrence_ids),
                *(ReservationInvalidatedEffect for _ in range(generation_count)),
            )
        if isinstance(event, ReconcileGenerationEvent):
            effect_types = (GenerationReconciledEffect,)
            if transition.decision.kind is AdmissionDecisionKind.QUARANTINED:
                effect_types += (QuarantineRecordedEffect,)
            return effect_types
        if isinstance(event, ExpireIdempotencyKeyEvent):
            return (IdempotencyExpiredEffect,)
        if isinstance(event, RolloverEpochEvent):
            invalidated_batch_ids = {
                record.batch.batch_id
                for record in self.state.batch_records
                if record.state
                in {
                    AdmissionState.RESERVED,
                    AdmissionState.PREPARED,
                    AdmissionState.HISTORY_STAGED,
                }
            }
            input_invalidation_count = sum(
                reservation.key.batch_id in invalidated_batch_ids
                for reservation in self.state.reservations
            )
            retained_request_ids = {
                record.batch.request_id
                for record in self.state.batch_records
                if record.state
                in {
                    AdmissionState.REQUEST_DISPATCHED,
                    AdmissionState.COMMITTED,
                    AdmissionState.INDETERMINATE,
                    AdmissionState.QUARANTINED,
                }
            }
            generation_invalidation_count = sum(
                generation.state
                in {
                    GenerationState.RESERVED,
                    GenerationState.STREAMING,
                    GenerationState.INDETERMINATE,
                }
                and generation.request_id not in retained_request_ids
                for generation in self.state.generation_reservations
            )
            occurrence_change_count = sum(
                record.state
                in {
                    AdmissionState.PROPOSED,
                    AdmissionState.RESERVED,
                    AdmissionState.PREPARED,
                    AdmissionState.HISTORY_STAGED,
                }
                for record in self.state.occurrence_records
            )
            return (
                *(
                    ReservationInvalidatedEffect
                    for _ in range(input_invalidation_count + generation_invalidation_count)
                ),
                *(OccurrenceStateChangedEffect for _ in range(occurrence_change_count)),
                EpochClosedEffect,
            )
        if isinstance(event, RequestReconciliationEvent):
            return (
                ReconciliationEscalationEffect
                if "deadline" in event.reason_code.casefold()
                else ReconciliationQueryRequestedEffect,
            )
        raise AssertionError(f"missing effect contract for {type(event).__name__}")

    def _availability(self, reserve_class: ReserveClass) -> tuple[int, int, int]:
        total_charged = sum(
            input_count + output_count for _, input_count, output_count in self.charges.values()
        )
        global_unallocated = 50 - total_charged
        synthesis_used = sum(
            input_count + output_count
            for charged_class, input_count, output_count in self.charges.values()
            if charged_class is ReserveClass.SYNTHESIS
        )
        final_used = sum(
            input_count + output_count
            for charged_class, input_count, output_count in self.charges.values()
            if charged_class is ReserveClass.FINAL_RESPONSE
        )
        synthesis_unused = 12 - synthesis_used
        final_unused = 12 - final_used
        ordinary_available = global_unallocated - synthesis_unused - final_unused
        if reserve_class is ReserveClass.SYNTHESIS:
            class_available = min(global_unallocated, synthesis_unused)
        elif reserve_class is ReserveClass.FINAL_RESPONSE:
            class_available = min(global_unallocated, final_unused)
        else:
            class_available = ordinary_available
        return global_unallocated, ordinary_available, class_available

    @rule(slot=_SLOT, reserve_class=_RESERVE_CLASS)
    def propose(self, slot: int, reserve_class: ReserveClass) -> None:
        if slot in self.occurrences:
            return
        occurrence = _occurrence(
            slot,
            reserve_class,
            window_epoch_id=self.state.snapshot.window_epoch_id,
            window_epoch_number=self.state.snapshot.window_epoch_number,
        )
        event = ProposeOccurrenceEvent(**self._fields("propose-occurrence"), occurrence=occurrence)
        transition = reduce_context_admission(self.state, event)
        assert transition.decision.kind is AdmissionDecisionKind.WOULD_ADMIT
        self._accept_publication(transition, event)
        self.occurrences[slot] = occurrence
        self.latest_propose_event = event

    @rule(
        slot=_SLOT,
        input_count=st.integers(min_value=1, max_value=15),
        generation_count=st.integers(min_value=0, max_value=8),
    )
    def reserve(self, slot: int, input_count: int, generation_count: int) -> None:
        if slot not in self.occurrences or slot in self.charges:
            return
        occurrence = self.occurrences[slot]
        batch = _batch(occurrence)
        event = ReserveRequestEvent(
            **self._fields("reserve-request"),
            batch=batch,
            snapshot_sequence=1,
            input_reservations=(_reservation(occurrence, batch, input_count),),
            generation_reservation=_generation(occurrence, batch, generation_count),
        )
        prior = self.state
        transition = reduce_context_admission(prior, event)
        _, _, class_available = self._availability(occurrence.reserve_class)
        expected_admit = input_count + generation_count <= class_available
        assert transition.decision.kind is (
            AdmissionDecisionKind.WOULD_ADMIT
            if expected_admit
            else AdmissionDecisionKind.WOULD_REJECT
        )
        if expected_admit:
            self._accept_publication(transition, event)
            self.charges[slot] = (
                occurrence.reserve_class,
                input_count,
                generation_count,
            )
        else:
            assert (
                replace(
                    transition.next_state,
                    processed_events=prior.processed_events,
                )
                == prior
            )
            self._accept_publication(transition, event)

    @rule()
    def stale_fence_is_rejected(self) -> None:
        old_snapshot = self.state.snapshot
        new_window_epoch_number = old_snapshot.window_epoch_number + 1
        receiver_authority = AuthoritySourceId("authority-state-machine")
        event = RolloverEpochEvent(
            **self._fields("rollover-epoch"),
            witness=AdmissionWitness(
                witness_id=AdmissionWitnessId(f"stale-rollover-witness-{self.event_sequence}"),
                kind=WitnessKind.EPOCH_ROLLOVER,
                window_epoch_id=old_snapshot.window_epoch_id,
                window_epoch_number=old_snapshot.window_epoch_number,
                snapshot_sequence=old_snapshot.snapshot_sequence,
                request_id=AdmissionRequestId("stale-rollover-request"),
                batch_id=AdmissionBatchId("stale-rollover-batch"),
                representation_revision=RepresentationRevision("stale-rollover-revision"),
                representation_binding_id=RepresentationBindingId("stale-rollover-binding"),
                occurrence_ids=(),
                authority_source_id=receiver_authority,
            ),
            fence_proof=EpochFenceProof(
                old_window_epoch_id=WindowEpochId("stale-window-epoch"),
                old_window_epoch_number=old_snapshot.window_epoch_number,
                new_window_epoch_id=WindowEpochId(
                    f"epoch-state-machine-{new_window_epoch_number}"
                ),
                new_window_epoch_number=new_window_epoch_number,
                receiver_authority_source_id=receiver_authority,
                fence_witness_id=AdmissionWitnessId(f"stale-fence-{self.event_sequence}"),
                highest_admitted_dispatch_sequence=self.state.admission_sequence.value,
            ),
            new_snapshot=ContextWindowSnapshot(
                protocol_version=CONTEXT_ADMISSION_PROTOCOL_VERSION,
                window_epoch_id=WindowEpochId(f"epoch-state-machine-{new_window_epoch_number}"),
                window_epoch_number=new_window_epoch_number,
                model_identity=ModelIdentity.anthropic("claude-state-machine"),
                tokenizer_identity=TokenizerIdentity("tokenizer-state-machine"),
                snapshot_sequence=1,
                active_count=50,
                hard_limit=100,
                remaining_count=50,
            ),
            protected_pools=_pool_specs(),
        )
        prior = self.state
        transition = reduce_context_admission(prior, event)
        assert transition.decision.kind is AdmissionDecisionKind.WOULD_REJECT
        assert replace(transition.next_state, processed_events=prior.processed_events) == prior
        self._accept_publication(transition, event)

    @rule(
        slot=_SLOT,
        input_count=st.integers(min_value=1, max_value=10),
    )
    def reserve_multi_member_batch(self, slot: int, input_count: int) -> None:
        other_slot = 1 if slot == 0 else 0
        if other_slot not in self.occurrences or slot not in self.occurrences:
            return
        if slot in self.charges or other_slot in self.charges:
            return
        first = self.occurrences[slot]
        second = self.occurrences[other_slot]
        if first.reserve_class is not second.reserve_class:
            return
        batch = _multi_batch((first, second))
        total = input_count * 2
        event = ReserveRequestEvent(
            **self._fields("reserve-multi"),
            batch=batch,
            snapshot_sequence=1,
            input_reservations=(_reservation_for_batch(batch, first, total),),
            generation_reservation=None,
        )
        prior = self.state
        transition = reduce_context_admission(prior, event)
        if transition.decision.kind is AdmissionDecisionKind.WOULD_REJECT:
            self._accept_publication(transition, event)
            return
        assert transition.decision.kind is AdmissionDecisionKind.WOULD_ADMIT
        self._accept_publication(transition, event)
        self.charges[slot] = (first.reserve_class, total, 0)
        self.charges[other_slot] = (second.reserve_class, 0, 0)

    @rule(accept_now=st.booleans())
    def prepare_stage_dispatch_and_accept(self, accept_now: bool) -> None:
        if not self.charges:
            return
        slot = min(self.charges)
        reserve_class, input_count, _ = self.charges[slot]
        if reserve_class is ReserveClass.ORDINARY:
            return
        occurrence = self.occurrences[slot]
        batch = _batch(occurrence)
        reserved = self._find_batch(batch.batch_id)
        if reserved is None or reserved.state is not AdmissionState.RESERVED:
            return
        reserved_input_count = input_count
        prepare_event = PrepareBatchEvent(
            **self._fields("prepare-batch"),
            batch_id=batch.batch_id,
            representation_revision=batch.manifest.representation_revision,
            representation_binding_id=batch.manifest.representation_binding_id,
            proposed_charge=reserved_input_count,
            measurement_kind=MeasurementKind.PROVIDER_EXACT,
            authority_source=AuthoritySourceId("authority-state-machine"),
        )
        transition = reduce_context_admission(self.state, prepare_event)
        assert transition.decision.kind is AdmissionDecisionKind.WOULD_ADMIT
        self._accept_publication(transition, prepare_event)
        skipped_dispatch = DispatchRequestEvent(
            **self._fields("dispatch-without-history-stage"),
            batch_id=batch.batch_id,
            witness=_witness(batch, occurrence, WitnessKind.REQUEST_INCLUDED),
        )
        prior = self.state
        rejected = reduce_context_admission(prior, skipped_dispatch)
        assert rejected.decision.kind is AdmissionDecisionKind.WOULD_REJECT
        assert replace(rejected.next_state, processed_events=prior.processed_events) == prior
        self._accept_publication(rejected, skipped_dispatch)
        stage_event = StageHistoryEvent(
            **self._fields("stage-history"),
            batch_id=batch.batch_id,
            witness=_witness(batch, occurrence, WitnessKind.HISTORY_STAGED),
        )
        transition = reduce_context_admission(self.state, stage_event)
        assert transition.decision.kind is AdmissionDecisionKind.WOULD_ADMIT
        self._accept_publication(transition, stage_event)
        generation = next(
            (
                record
                for record in self.state.generation_reservations
                if record.batch_id == batch.batch_id
            ),
            None,
        )
        if generation is not None:
            premature_generation = StartGenerationEvent(
                **self._fields("generation-before-dispatch"),
                generation_reservation_id=generation.generation_reservation_id,
                witness=_witness(batch, occurrence, WitnessKind.REQUEST_INCLUDED),
            )
            prior = self.state
            rejected = reduce_context_admission(prior, premature_generation)
            assert rejected.decision.kind is AdmissionDecisionKind.WOULD_REJECT
            assert replace(rejected.next_state, processed_events=prior.processed_events) == prior
            self._accept_publication(rejected, premature_generation)
        dispatch_event = DispatchRequestEvent(
            **self._fields("dispatch-request"),
            batch_id=batch.batch_id,
            witness=_witness(batch, occurrence, WitnessKind.REQUEST_INCLUDED),
        )
        transition = reduce_context_admission(self.state, dispatch_event)
        assert transition.decision.kind is AdmissionDecisionKind.WOULD_ADMIT
        self._accept_publication(transition, dispatch_event)
        if not accept_now:
            return
        accept_event = AcceptInputEvent(
            **self._fields("accept-input"),
            batch_id=batch.batch_id,
            witness=_witness(batch, occurrence, WitnessKind.PROVIDER_ACCEPTED),
            final_manifest_revision=batch.manifest.representation_revision,
            final_manifest=batch.manifest,
            exact_input_charge=reserved_input_count,
            measurement_kind=MeasurementKind.PROVIDER_EXACT,
            authority_source=AuthoritySourceId("authority-state-machine"),
            representation_binding_witness=_binding(batch),
        )
        transition = reduce_context_admission(self.state, accept_event)
        assert transition.decision.kind is AdmissionDecisionKind.WOULD_ADMIT
        self._accept_publication(transition, accept_event)

    @rule()
    def prepare_and_mark_indeterminate(self) -> None:
        if not self.charges:
            return
        slot = min(self.charges)
        _, input_count, _ = self.charges[slot]
        occurrence = self.occurrences[slot]
        batch = _batch(occurrence)
        reserved = self._find_batch(batch.batch_id)
        if reserved is None or reserved.state is not AdmissionState.RESERVED:
            return
        prepare_event = PrepareBatchEvent(
            **self._fields("prepare-batch"),
            batch_id=batch.batch_id,
            representation_revision=batch.manifest.representation_revision,
            representation_binding_id=batch.manifest.representation_binding_id,
            proposed_charge=input_count,
            measurement_kind=MeasurementKind.PROVIDER_EXACT,
            authority_source=AuthoritySourceId("authority-state-machine"),
        )
        prepared = reduce_context_admission(self.state, prepare_event)
        assert prepared.decision.kind is AdmissionDecisionKind.WOULD_ADMIT
        self._accept_publication(prepared, prepare_event)
        indeterminate_event = MarkIndeterminateEvent(
            **self._fields("mark-indeterminate"),
            batch_id=batch.batch_id,
            reason_code="provider-result-lost",
        )
        marked = reduce_context_admission(self.state, indeterminate_event)
        assert marked.decision.kind is AdmissionDecisionKind.WOULD_ADMIT
        self._accept_publication(marked, indeterminate_event)

    @rule(rollback=st.booleans())
    def release_or_rollback_preacceptance(self, rollback: bool) -> None:
        for slot in sorted(self.charges):
            occurrence = self.occurrences.get(slot)
            if occurrence is None:
                continue
            batch = _batch(occurrence)
            record = self._find_batch(batch.batch_id)
            if record is None:
                continue
            if rollback:
                if record.state not in {
                    AdmissionState.HISTORY_STAGED,
                    AdmissionState.REQUEST_DISPATCHED,
                }:
                    continue
                event: Any = RollbackAdmissionEvent(
                    **self._fields("rollback-admission"),
                    batch_id=batch.batch_id,
                    witness=_witness(batch, occurrence, WitnessKind.ROLLBACK),
                )
            else:
                if record.state not in {
                    AdmissionState.RESERVED,
                    AdmissionState.PREPARED,
                    AdmissionState.HISTORY_STAGED,
                    AdmissionState.REQUEST_DISPATCHED,
                }:
                    continue
                event = ReleaseNonAdmissionEvent(
                    **self._fields("release-non-admission"),
                    batch_id=batch.batch_id,
                    witness=_witness(
                        batch,
                        occurrence,
                        WitnessKind.NON_ADMISSION,
                    ),
                )
            transition = reduce_context_admission(self.state, event)
            assert transition.decision.kind is AdmissionDecisionKind.WOULD_ADMIT
            self._accept_publication(transition, event)
            reserve_class, _, _ = self.charges[slot]
            self.charges[slot] = (reserve_class, 0, 0)
            return

    @rule(rollback=st.booleans())
    def resolve_indeterminate_nonacceptance(self, rollback: bool) -> None:
        for slot in sorted(self.charges):
            occurrence = self.occurrences.get(slot)
            if occurrence is None:
                continue
            batch = _batch(occurrence)
            record = self._find_batch(batch.batch_id)
            if record is None or record.state is not AdmissionState.INDETERMINATE:
                continue
            if rollback:
                event: Any = ResolveIndeterminateRollbackEvent(
                    **self._fields("resolve-indeterminate-rollback"),
                    batch_id=batch.batch_id,
                    witness=_witness(batch, occurrence, WitnessKind.ROLLBACK),
                )
            else:
                event = ResolveIndeterminateNonAdmissionEvent(
                    **self._fields("resolve-indeterminate-non-admission"),
                    batch_id=batch.batch_id,
                    witness=_witness(
                        batch,
                        occurrence,
                        WitnessKind.NON_ADMISSION,
                    ),
                )
            transition = reduce_context_admission(self.state, event)
            assert transition.decision.kind is AdmissionDecisionKind.WOULD_ADMIT
            self._accept_publication(transition, event)
            reserve_class, _, _ = self.charges[slot]
            self.charges[slot] = (reserve_class, 0, 0)
            return

    @rule(exact_usage=st.integers(min_value=0, max_value=16))
    def start_and_reconcile_generation(self, exact_usage: int) -> None:
        for generation in self.state.generation_reservations:
            if generation.state is not GenerationState.RESERVED:
                continue
            slot_and_occurrence = next(
                (
                    (slot, occurrence)
                    for slot, occurrence in self.occurrences.items()
                    if _batch(occurrence).batch_id == generation.batch_id
                ),
                None,
            )
            if slot_and_occurrence is None:
                continue
            slot, occurrence = slot_and_occurrence
            batch = _batch(occurrence)
            record = self._find_batch(batch.batch_id)
            if record is None or record.state not in {
                AdmissionState.HISTORY_STAGED,
                AdmissionState.REQUEST_DISPATCHED,
                AdmissionState.COMMITTED,
                AdmissionState.QUARANTINED,
            }:
                continue
            start = StartGenerationEvent(
                **self._fields("start-generation"),
                generation_reservation_id=generation.generation_reservation_id,
                witness=_witness(
                    batch,
                    occurrence,
                    WitnessKind.REQUEST_INCLUDED,
                ),
            )
            started = reduce_context_admission(self.state, start)
            assert started.decision.kind is AdmissionDecisionKind.WOULD_ADMIT
            self._accept_publication(started, start)
            reconcile = ReconcileGenerationEvent(
                **self._fields("reconcile-generation"),
                generation_reservation_id=generation.generation_reservation_id,
                output_usage_witness=_witness(
                    batch,
                    occurrence,
                    WitnessKind.OUTPUT_USAGE,
                ),
                exact_output_usage=exact_usage,
            )
            reconciled = reduce_context_admission(self.state, reconcile)
            expected = (
                AdmissionDecisionKind.QUARANTINED
                if exact_usage > generation.maximum_allowance
                else AdmissionDecisionKind.WOULD_ADMIT
            )
            assert reconciled.decision.kind is expected
            self._accept_publication(reconciled, reconcile)
            reserve_class, input_count, _ = self.charges[slot]
            self.charges[slot] = (reserve_class, input_count, 0)
            return

    @rule()
    def expire_terminal_idempotency_key(self) -> None:
        for record in self.state.idempotency_records:
            batch = record.original_descriptor.batch
            occurrence = next(
                (
                    occurrence
                    for occurrence in self.occurrences.values()
                    if occurrence.occurrence_id in batch.occurrence_ids
                ),
                None,
            )
            batch_record = self._find_batch(batch.batch_id)
            if (
                occurrence is None
                or batch_record is None
                or batch_record.state
                not in {
                    AdmissionState.COMMITTED,
                    AdmissionState.RELEASED,
                    AdmissionState.ROLLED_BACK,
                    AdmissionState.QUARANTINED,
                }
            ):
                continue
            event = ExpireIdempotencyKeyEvent(
                **self._fields("expire-idempotency-key"),
                reservation_key=record.reservation_key,
                expiry_witness=_witness(
                    batch,
                    occurrence,
                    WitnessKind.IDEMPOTENCY_EXPIRY,
                ),
            )
            transition = reduce_context_admission(self.state, event)
            assert transition.decision.kind is AdmissionDecisionKind.WOULD_ADMIT
            self._accept_publication(transition, event)
            return

    @rule()
    def rollover_preserves_dispatched_and_indeterminate_charge(self) -> None:
        before_charges = dict(self.charges)
        retained_batch_ids = {
            record.batch.batch_id
            for record in self.state.batch_records
            if record.state
            in {
                AdmissionState.REQUEST_DISPATCHED,
                AdmissionState.INDETERMINATE,
            }
        }
        retained_slots = {
            slot
            for slot, occurrence in self.occurrences.items()
            if _batch(occurrence).batch_id in retained_batch_ids
        }
        old_snapshot = self.state.snapshot
        new_window_epoch_number = old_snapshot.window_epoch_number + 1
        new_window_epoch_id = WindowEpochId(f"epoch-state-machine-{new_window_epoch_number}")
        event_fields = self._fields("rollover-epoch")
        witness_suffix = (
            f"{old_snapshot.window_epoch_number}-to-"
            f"{new_window_epoch_number}-{self.event_sequence}"
        )
        proof = EpochFenceProof(
            old_window_epoch_id=old_snapshot.window_epoch_id,
            old_window_epoch_number=old_snapshot.window_epoch_number,
            new_window_epoch_id=new_window_epoch_id,
            new_window_epoch_number=new_window_epoch_number,
            receiver_authority_source_id=AuthoritySourceId("authority-state-machine"),
            fence_witness_id=AdmissionWitnessId(f"fence-{witness_suffix}"),
            highest_admitted_dispatch_sequence=sum(
                isinstance(record.event, DispatchRequestEvent)
                and record.original_decision.kind is AdmissionDecisionKind.WOULD_ADMIT
                and record.event.witness.window_epoch_id == old_snapshot.window_epoch_id
                and record.event.witness.window_epoch_number == old_snapshot.window_epoch_number
                for record in self.state.processed_events
            ),
        )
        rollover_event = RolloverEpochEvent(
            **event_fields,
            witness=AdmissionWitness(
                witness_id=AdmissionWitnessId(f"rollover-witness-{witness_suffix}"),
                kind=WitnessKind.EPOCH_ROLLOVER,
                window_epoch_id=old_snapshot.window_epoch_id,
                window_epoch_number=old_snapshot.window_epoch_number,
                snapshot_sequence=old_snapshot.snapshot_sequence,
                request_id=AdmissionRequestId("rollover-request"),
                batch_id=AdmissionBatchId("rollover-batch"),
                representation_revision=RepresentationRevision("rollover-revision"),
                representation_binding_id=RepresentationBindingId("rollover-binding"),
                occurrence_ids=(),
                authority_source_id=AuthoritySourceId("authority-state-machine"),
            ),
            fence_proof=proof,
            new_snapshot=ContextWindowSnapshot(
                protocol_version=CONTEXT_ADMISSION_PROTOCOL_VERSION,
                window_epoch_id=new_window_epoch_id,
                window_epoch_number=new_window_epoch_number,
                model_identity=ModelIdentity.anthropic("claude-state-machine"),
                tokenizer_identity=TokenizerIdentity("tokenizer-state-machine"),
                snapshot_sequence=1,
                active_count=50,
                hard_limit=100,
                remaining_count=50,
            ),
            protected_pools=_pool_specs(),
        )
        transition = reduce_context_admission(self.state, rollover_event)
        assert transition.decision.kind is AdmissionDecisionKind.WOULD_ADMIT
        new_state = transition.next_state
        assert isinstance(new_state, ActiveContextAdmissionState)
        assert len(new_state.closed_epochs) >= 1
        prior_audit = new_state.closed_epochs[-1]
        assert prior_audit.retained_unresolved_count == sum(
            before_charges[slot][1] for slot in retained_slots
        )
        for record in new_state.batch_records:
            if record.state in {
                AdmissionState.REQUEST_DISPATCHED,
                AdmissionState.INDETERMINATE,
            }:
                owner = record.batch.protected_pool_owner_id
                if owner is None:
                    assert record.batch.reserve_class is ReserveClass.ORDINARY
        self._accept_publication(transition, rollover_event)
        self.occurrences = {}
        self.charges = {}
        self.last_rollover_retention = prior_audit.retained_unresolved_count

    @precondition(lambda self: self.latest_replayable_event is not None)
    @rule()
    def identical_replay_is_equivalent(self) -> None:
        assert self.latest_replayable_event is not None
        event_type = type(self.latest_replayable_event)
        serialized_event = self.latest_replayable_event.to_dict()
        restored_event = event_type.from_dict(serialized_event)
        transition = reduce_context_admission(self.state, restored_event)
        assert transition.decision.kind is AdmissionDecisionKind.NOOP_IDEMPOTENT
        assert transition.next_state == self.state
        assert transition.effects == ()

    @rule()
    def generated_stream_replay_is_deterministic(self) -> None:
        replay = replay_context_admission(_uninitialized(), tuple(self.events))
        assert replay.final_state == self.state
        assert len(replay.transitions) == len(self.events)
        assert [transition.effects for transition in replay.transitions] == self.published_effects

    @precondition(lambda self: self.latest_propose_event is not None)
    @rule()
    def changed_intent_conflict_does_not_mutate(self) -> None:
        assert self.latest_propose_event is not None
        changed = replace(
            self.latest_propose_event,
            occurrence=replace(
                self.latest_propose_event.occurrence,
                representation_revision=RepresentationRevision(
                    f"conflicting-{self.event_sequence}"
                ),
            ),
        )
        transition = reduce_context_admission(self.state, changed)
        assert transition.decision.kind is AdmissionDecisionKind.CONFLICT
        assert transition.next_state == self.state

    @precondition(lambda self: bool(self.charges))
    @rule()
    def reconciliation_deadline_never_releases(self) -> None:
        slot = min(self.charges)
        occurrence = self.occurrences[slot]
        before = dict(self.charges)
        event = RequestReconciliationEvent(
            **self._fields("request-reconciliation"),
            target_id=_batch(occurrence).batch_id,
            reason_code="deadline-observed",
        )
        transition = reduce_context_admission(self.state, event)
        assert not any("Released" in type(effect).__name__ for effect in transition.effects)
        self._accept_publication(transition, event)
        assert self.charges == before

    @invariant()
    def capacity_is_non_negative_and_protected_pools_are_isolated(self) -> None:
        global_unallocated, ordinary_available, _ = self._availability(ReserveClass.ORDINARY)
        assert global_unallocated >= 0
        assert ordinary_available >= 0
        for reserve_class in (
            ReserveClass.SYNTHESIS,
            ReserveClass.FINAL_RESPONSE,
        ):
            _, _, pool_available = self._availability(reserve_class)
            assert pool_available >= 0

    @invariant()
    def state_round_trip_is_canonical(self) -> None:
        restored = ActiveContextAdmissionState.from_dict(self.state.to_dict())
        assert restored == self.state
        assert restored.to_dict() == self.state.to_dict()

    @invariant()
    def revisions_sequences_and_closed_audits_are_monotonic(self) -> None:
        assert self.state.aggregate_revision.value >= self.last_revision
        assert self.state.admission_sequence.value >= self.last_admission_sequence
        assert len(self.state.closed_epochs) >= self.closed_audit_count
        self.last_revision = self.state.aggregate_revision.value
        self.last_admission_sequence = self.state.admission_sequence.value
        self.closed_audit_count = len(self.state.closed_epochs)

    @invariant()
    def no_batch_is_partially_reserved_or_double_charged(self) -> None:
        charged_occurrences = {self.occurrences[slot].occurrence_id for slot in self.charges}
        assert len(charged_occurrences) == len(self.charges)
        for record in self.state.batch_records:
            member_states = {
                occurrence_record.state
                for occurrence_record in self.state.occurrence_records
                if occurrence_record.occurrence.occurrence_id in record.batch.occurrence_ids
            }
            assert len(member_states) == 1
            assert member_states <= {
                AdmissionState.RESERVED,
                AdmissionState.PREPARED,
                AdmissionState.HISTORY_STAGED,
                AdmissionState.REQUEST_DISPATCHED,
                AdmissionState.COMMITTED,
                AdmissionState.RELEASED,
                AdmissionState.ROLLED_BACK,
                AdmissionState.INVALIDATED,
                AdmissionState.INDETERMINATE,
                AdmissionState.QUARANTINED,
            }


ContextAdmissionStateMachine.TestCase.settings = settings(
    max_examples=24,
    stateful_step_count=12,
    deadline=None,
)
TestContextAdmissionStateMachine = ContextAdmissionStateMachine.TestCase
