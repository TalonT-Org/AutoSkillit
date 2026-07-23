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
    ContextLineage,
    ContextSessionId,
    ContextThreadId,
    ContextWindowSnapshot,
    DispatchRequestEvent,
    EpochFenceProof,
    GenerationReservationId,
    GenerationReservationRecord,
    GenerationState,
    IdempotencyNamespace,
    MeasurementKind,
    ModelIdentity,
    ModelItemId,
    OpenEpochEvent,
    PrepareBatchEvent,
    ProducerInstanceId,
    ProducerSurface,
    ProposeOccurrenceEvent,
    ProtectedPoolOwnerId,
    ProtectedPoolSpec,
    RepresentationBindingWitness,
    RepresentationRevision,
    RequestReconciliationEvent,
    ReserveClass,
    ReserveRequestEvent,
    RolloverEpochEvent,
    StageHistoryEvent,
    TokenizerIdentity,
    ToolCallId,
    TurnId,
    UninitializedContextAdmissionState,
    WindowEpochId,
    WitnessKind,
    reduce_context_admission,
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


def _occurrence(slot: int, reserve_class: ReserveClass) -> AdmissionOccurrence:
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
            fork_occurrence_id=None,
            turn_id=TurnId(f"turn-{slot}"),
            producer_surface=surface,
            producer_instance_id=ProducerInstanceId(f"producer-{slot}"),
            tool_call_id=ToolCallId(f"tool-{slot}"),
            model_item_id=ModelItemId(f"item-{slot}"),
            dispatch_identity=None,
            attempt_id=AdmissionAttemptId(f"attempt-{slot}"),
            delivery_occurrence_id=None,
            window_epoch_id=WindowEpochId("epoch-state-machine"),
            window_epoch_number=1,
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
    slots = tuple(occurrence.occurrence_id.value.rsplit("-", 1)[-1] for occurrence in occurrences)
    request_id = AdmissionRequestId("request-multi-" + "-".join(slots))
    reserve_class = occurrences[0].reserve_class
    if any(occurrence.reserve_class is not reserve_class for occurrence in occurrences):
        msg = "multi-member batch must share one reserve class"
        raise ValueError(msg)
    manifest = CanonicalRepresentationManifest(
        request_id=request_id,
        representation_revision=occurrences[0].representation_revision,
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
    key = AdmissionReservationKey(
        idempotency_namespace=_namespace("reserve-request"),
        protocol_version=CONTEXT_ADMISSION_PROTOCOL_VERSION,
        window_epoch_id=WindowEpochId("epoch-state-machine"),
        window_epoch_number=1,
        batch_id=batch.batch_id,
        reserve_class=occurrence.reserve_class,
        protected_pool_owner_id=_owner(occurrence.reserve_class),
        occurrence_revisions=((occurrence.occurrence_id, occurrence.representation_revision),),
    )
    return AdmissionReservation(
        reservation_id=AdmissionReservationId(f"reservation-{occurrence.occurrence_id.value}"),
        key=key,
        window_epoch_id=WindowEpochId("epoch-state-machine"),
        window_epoch_number=1,
        snapshot_sequence=1,
        reserve_class=occurrence.reserve_class,
        protected_pool_owner_id=_owner(occurrence.reserve_class),
        occurrence_ids=(occurrence.occurrence_id,),
        reserved_count=count,
    )


def _reservation_for_batch(batch: AdmissionBatch, count: int) -> AdmissionReservation:
    occurrence_ids = batch.occurrence_ids
    slot = occurrence_ids[0].value.rsplit("-", 1)[-1]
    key = AdmissionReservationKey(
        idempotency_namespace=_namespace("reserve-multi"),
        protocol_version=CONTEXT_ADMISSION_PROTOCOL_VERSION,
        window_epoch_id=WindowEpochId("epoch-state-machine"),
        window_epoch_number=1,
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
        window_epoch_id=WindowEpochId("epoch-state-machine"),
        window_epoch_number=1,
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
        response_id=ModelItemId(f"response-{occurrence.occurrence_id.value}"),
        window_epoch_id=WindowEpochId("epoch-state-machine"),
        window_epoch_number=1,
        snapshot_sequence=1,
        reserve_class=occurrence.reserve_class,
        protected_pool_owner_id=_owner(occurrence.reserve_class),
        maximum_allowance=count,
        state=GenerationState.RESERVED,
        exact_terminal_usage=None,
        witness_ids=(),
    )


def _witness(
    batch: AdmissionBatch,
    kind: WitnessKind,
    *,
    occurrence_ids: tuple[AdmissionOccurrenceId, ...] | None = None,
) -> AdmissionWitness:
    return AdmissionWitness(
        witness_id=AdmissionWitnessId(f"{kind.value}-witness-{batch.batch_id.value}"),
        kind=kind,
        window_epoch_id=WindowEpochId("epoch-state-machine"),
        window_epoch_number=1,
        snapshot_sequence=1,
        request_id=batch.request_id,
        batch_id=batch.batch_id,
        representation_revision=batch.manifest.representation_revision,
        occurrence_ids=occurrence_ids or batch.occurrence_ids,
        authority_source_id=AuthoritySourceId("authority-state-machine"),
    )


def _binding(batch: AdmissionBatch) -> RepresentationBindingWitness:
    bound_revision = batch.manifest.representation_revision
    return RepresentationBindingWitness(
        counted_representation_revision=bound_revision,
        dispatched_representation_revision=bound_revision,
        final_manifest_revision=bound_revision,
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
        self.state = transition.next_state
        self.last_revision = self.state.aggregate_revision.value
        self.last_admission_sequence = self.state.admission_sequence.value
        self.latest_replayable_event = event

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
        occurrence = _occurrence(slot, reserve_class)
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
            assert transition.next_state == prior

    @rule(slot=_SLOT)
    def stale_fence_is_rejected(self, slot: int) -> None:
        if slot not in self.occurrences or slot in self.charges:
            return
        occurrence = self.occurrences[slot]
        batch = _batch(occurrence)
        event = ReserveRequestEvent(
            **{
                **self._fields("reserve-request"),
                "expected_aggregate_revision": AggregateRevision(
                    max(0, self.state.aggregate_revision.value - 1)
                ),
            },
            batch=batch,
            snapshot_sequence=1,
            input_reservations=(_reservation(occurrence, batch, 1),),
            generation_reservation=_generation(occurrence, batch, 0),
        )
        prior = self.state
        transition = reduce_context_admission(prior, event)
        assert transition.decision.kind is AdmissionDecisionKind.WOULD_REJECT
        assert transition.next_state == prior

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
            input_reservations=(_reservation_for_batch(batch, total),),
            generation_reservation=None,
        )
        prior = self.state
        transition = reduce_context_admission(prior, event)
        if transition.decision.kind is AdmissionDecisionKind.WOULD_REJECT:
            assert transition.next_state == prior
            return
        assert transition.decision.kind is AdmissionDecisionKind.WOULD_ADMIT
        self._accept_publication(transition, event)
        self.charges[slot] = (first.reserve_class, total, 0)
        self.charges[other_slot] = (second.reserve_class, 0, 0)

    @rule()
    def prepare_stage_dispatch_and_accept(self) -> None:
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
            proposed_charge=reserved_input_count,
            measurement_kind=MeasurementKind.PROVIDER_EXACT,
            authority_source_id=AuthoritySourceId("authority-state-machine"),
        )
        transition = reduce_context_admission(self.state, prepare_event)
        assert transition.decision.kind is AdmissionDecisionKind.WOULD_ADMIT
        self._accept_publication(transition, prepare_event)
        stage_event = StageHistoryEvent(
            **self._fields("stage-history"),
            batch_id=batch.batch_id,
            witness=_witness(batch, WitnessKind.HISTORY_STAGED),
        )
        transition = reduce_context_admission(self.state, stage_event)
        assert transition.decision.kind is AdmissionDecisionKind.WOULD_ADMIT
        self._accept_publication(transition, stage_event)
        dispatch_event = DispatchRequestEvent(
            **self._fields("dispatch-request"),
            batch_id=batch.batch_id,
            witness=_witness(batch, WitnessKind.REQUEST_INCLUDED),
        )
        transition = reduce_context_admission(self.state, dispatch_event)
        assert transition.decision.kind is AdmissionDecisionKind.WOULD_ADMIT
        self._accept_publication(transition, dispatch_event)
        accept_event = AcceptInputEvent(
            **self._fields("accept-input"),
            batch_id=batch.batch_id,
            witness=_witness(batch, WitnessKind.PROVIDER_ACCEPTED),
            final_manifest_revision=batch.manifest.representation_revision,
            exact_input_charge=reserved_input_count,
            measurement_kind=MeasurementKind.PROVIDER_EXACT,
            authority_source_id=AuthoritySourceId("authority-state-machine"),
            representation_binding_witness=_binding(batch),
        )
        transition = reduce_context_admission(self.state, accept_event)
        assert transition.decision.kind is AdmissionDecisionKind.WOULD_ADMIT
        self._accept_publication(transition, accept_event)

    @rule()
    def rollover_preserves_dispatched_and_indeterminate_charge(self) -> None:
        before_charges = dict(self.charges)
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
            highest_admitted_dispatch_sequence=self.state.admission_sequence.value,
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
        assert prior_audit.retained_unresolved_count >= 0
        for record in new_state.batch_records:
            if record.state in {
                AdmissionState.REQUEST_DISPATCHED,
                AdmissionState.INDETERMINATE,
            }:
                owner = record.batch.protected_pool_owner_id
                if owner is None:
                    assert record.batch.reserve_class is ReserveClass.ORDINARY
        if before_charges:
            self._accept_publication(transition, rollover_event)
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
