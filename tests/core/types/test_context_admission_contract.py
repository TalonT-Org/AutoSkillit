"""Freeze the protocol-v1 cumulative context-admission value contract."""

from __future__ import annotations

import dataclasses
from dataclasses import FrozenInstanceError, fields, replace
from typing import get_args

import pytest

from autoskillit.core import (
    CONTEXT_ADMISSION_PROTOCOL_VERSION,
    AcceptInputEvent,
    ActiveContextAdmissionState,
    AdmissionAttemptId,
    AdmissionBatch,
    AdmissionBatchId,
    AdmissionBatchRecord,
    AdmissionDecision,
    AdmissionDecisionKind,
    AdmissionEffect,
    AdmissionEventId,
    AdmissionOccurrence,
    AdmissionOccurrenceId,
    AdmissionOccurrenceRecord,
    AdmissionRequestId,
    AdmissionReservationId,
    AdmissionReservationKey,
    AdmissionSequence,
    AdmissionState,
    AdmissionTransition,
    AdmissionWitness,
    AdmissionWitnessId,
    AgentInstanceId,
    AggregateRevision,
    AuthoritySourceId,
    CanonicalRepresentationManifest,
    CanonicalSpanId,
    CanonicalSpanOwner,
    ChargeDomain,
    ClosedEpochAudit,
    ContextAdmissionEvent,
    ContextAdmissionState,
    ContextAdmissionValidationError,
    ContextLineage,
    ContextSessionId,
    ContextThreadId,
    ContextWindowSnapshot,
    CoverageEvidence,
    CoverageEvidenceKind,
    CoverageState,
    DeliveryOccurrenceId,
    DispatchIdentity,
    ForkOccurrenceId,
    GenerationReservationId,
    GenerationState,
    IdempotencyNamespace,
    MeasurementKind,
    ModelIdentity,
    ModelItemId,
    PrepareBatchEvent,
    ProducerInstanceId,
    ProducerSurface,
    ProtectedPoolOwnerId,
    ProtectedPoolSpec,
    RepresentationBindingWitness,
    RepresentationRevision,
    ReserveClass,
    TokenizerIdentity,
    ToolCallId,
    TurnId,
    UninitializedContextAdmissionState,
    UnsupportedContextAdmissionProtocolError,
    WindowEpochId,
    WitnessKind,
)

pytestmark = [pytest.mark.layer("core"), pytest.mark.small]


EXPECTED_ENUM_MEMBERS = {
    AdmissionState: (
        "PROPOSED",
        "RESERVED",
        "PREPARED",
        "HISTORY_STAGED",
        "REQUEST_DISPATCHED",
        "COMMITTED",
        "RELEASED",
        "ROLLED_BACK",
        "INVALIDATED",
        "INDETERMINATE",
        "QUARANTINED",
    ),
    AdmissionDecisionKind: (
        "WOULD_ADMIT",
        "WOULD_REJECT",
        "WATERMARK_UNAVAILABLE",
        "UPSTREAM_GATED",
        "NOOP_IDEMPOTENT",
        "CONFLICT",
        "IDEMPOTENCY_EXPIRED",
        "QUARANTINED",
    ),
    ChargeDomain: ("INPUT_CONTEXT", "OUTPUT_GENERATION"),
    GenerationState: (
        "RESERVED",
        "STREAMING",
        "RECONCILED",
        "INDETERMINATE",
        "QUARANTINED",
    ),
    MeasurementKind: (
        "PROVIDER_EXACT",
        "TOKENIZER_EXACT",
        "HOST_ESTIMATE",
        "BYTE_EMERGENCY",
    ),
    CoverageState: ("VERIFIED", "PARTIAL", "UPSTREAM_GATED"),
    CoverageEvidenceKind: (
        "AUTOSKILLIT_SOURCE",
        "CODEX_SOURCE",
        "CODEX_OFFICIAL_DOC",
        "CODEX_RUNTIME_PROBE",
        "INFERENCE",
    ),
    ReserveClass: ("ORDINARY", "SYNTHESIS", "FINAL_RESPONSE"),
    WitnessKind: (
        "EPOCH_SNAPSHOT",
        "INPUT_COUNTED",
        "HISTORY_STAGED",
        "REPRESENTATION_BOUND",
        "REQUEST_INCLUDED",
        "PROVIDER_ACCEPTED",
        "OUTPUT_USAGE",
        "TRUNCATION",
        "NON_ADMISSION",
        "ROLLBACK",
        "RECONCILIATION",
        "IDEMPOTENCY_EXPIRY",
        "EPOCH_FENCE",
        "EPOCH_ROLLOVER",
    ),
}

EXPECTED_PRODUCER_SURFACES = (
    "NATIVE_SHELL",
    "UNIFIED_EXEC_AND_WRITE_STDIN",
    "APPLY_PATCH",
    "AUTOSKILLIT_MCP",
    "EXTERNAL_MCP",
    "AUTOSKILLIT_LOCAL_FUNCTION",
    "OTHER_LOCAL_FUNCTION",
    "MCP_RESOURCE",
    "CLIENT_PROVIDER_RETRIEVAL",
    "CODE_MODE_AGGREGATE",
    "HOSTED_SPECIALIZED_TOOL",
    "HOOK_FEEDBACK",
    "TOOL_ARGUMENT",
    "TOOL_RESULT_ENVELOPE",
    "USER_PROMPT",
    "ASSISTANT_OUTPUT_HISTORY",
    "SKILL_PLUGIN_CONTEXT",
    "OTHER_CONTEXT_INJECTION",
    "HEADLESS_CHILD_PROMPT",
    "PARENT_VISIBLE_CHILD_DELIVERY",
    "COMPACTION_MODEL_WINDOW_TRANSITION",
)

EXPECTED_EVENT_TYPES = {
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
}

EXPECTED_EFFECT_TYPES = {
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
}

OPAQUE_STRING_TYPES = (
    ContextSessionId,
    AgentInstanceId,
    ContextThreadId,
    ForkOccurrenceId,
    TurnId,
    ProducerInstanceId,
    ToolCallId,
    ModelItemId,
    AdmissionRequestId,
    AdmissionBatchId,
    WindowEpochId,
    TokenizerIdentity,
    CanonicalSpanId,
    AdmissionOccurrenceId,
    AdmissionAttemptId,
    DeliveryOccurrenceId,
    AdmissionEventId,
    AdmissionReservationId,
    AdmissionWitnessId,
    AuthoritySourceId,
    GenerationReservationId,
    ProtectedPoolOwnerId,
    RepresentationRevision,
)


def _field_names(value_type: type[object]) -> tuple[str, ...]:
    return tuple(field.name for field in fields(value_type))


def _lineage(
    *,
    surface: ProducerSurface = ProducerSurface.TOOL_RESULT_ENVELOPE,
    span_suffix: str = "1",
    dispatch_identity: DispatchIdentity | None = None,
) -> ContextLineage:
    return ContextLineage(
        root_session_id=ContextSessionId("session-root"),
        current_session_id=ContextSessionId("session-current"),
        root_agent_id=AgentInstanceId("agent-root"),
        current_agent_id=AgentInstanceId("agent-current"),
        parent_agent_id=None,
        root_thread_id=ContextThreadId("thread-root"),
        current_thread_id=ContextThreadId("thread-current"),
        parent_thread_id=None,
        fork_occurrence_id=None,
        turn_id=TurnId("turn-1"),
        producer_surface=surface,
        producer_instance_id=ProducerInstanceId(f"producer-{span_suffix}"),
        tool_call_id=ToolCallId(f"tool-{span_suffix}"),
        model_item_id=ModelItemId(f"item-{span_suffix}"),
        dispatch_identity=dispatch_identity,
        attempt_id=AdmissionAttemptId(f"attempt-{span_suffix}"),
        delivery_occurrence_id=None,
        window_epoch_id=WindowEpochId("epoch-1"),
        window_epoch_number=1,
    )


def _occurrence(
    occurrence_id: str = "occurrence-1",
    *,
    span_id: str = "span-1",
    revision: str = "representation-1",
    surface: ProducerSurface = ProducerSurface.TOOL_RESULT_ENVELOPE,
) -> AdmissionOccurrence:
    return AdmissionOccurrence(
        occurrence_id=AdmissionOccurrenceId(occurrence_id),
        lineage=_lineage(surface=surface, span_suffix=occurrence_id),
        reserve_class=ReserveClass.ORDINARY,
        producer_surface=surface,
        predicted_authoritative_maximum=7,
        representation_revision=RepresentationRevision(revision),
        owned_span_ids=(CanonicalSpanId(span_id),),
    )


def _manifest(*occurrences: AdmissionOccurrence) -> CanonicalRepresentationManifest:
    return CanonicalRepresentationManifest(
        request_id=AdmissionRequestId("request-1"),
        representation_revision=RepresentationRevision("representation-1"),
        span_owners=tuple(
            CanonicalSpanOwner(span_id=span_id, occurrence_id=occurrence.occurrence_id)
            for occurrence in occurrences
            for span_id in occurrence.owned_span_ids
        ),
        assembler_identity=ProducerInstanceId("assembler-1"),
        assembler_witness_id=AdmissionWitnessId("assembler-witness-1"),
    )


def _batch(batch_id: str, occurrences: tuple[AdmissionOccurrence, ...]) -> AdmissionBatch:
    return AdmissionBatch(
        batch_id=AdmissionBatchId(batch_id),
        request_id=AdmissionRequestId("request-1"),
        occurrence_ids=tuple(occurrence.occurrence_id for occurrence in occurrences),
        reserve_class=ReserveClass.ORDINARY,
        protected_pool_owner_id=None,
        manifest=_manifest(*occurrences),
    )


def _namespace(operation_kind: str) -> IdempotencyNamespace:
    return IdempotencyNamespace(caller_scope="test-caller", operation_kind=operation_kind)


def _witness(
    batch: AdmissionBatch,
    kind: WitnessKind,
    *,
    witness: str | None = None,
) -> AdmissionWitness:
    return AdmissionWitness(
        witness_id=AdmissionWitnessId(witness or f"{kind.value}-witness-{batch.batch_id.value}"),
        kind=kind,
        window_epoch_id=WindowEpochId("epoch-1"),
        window_epoch_number=1,
        snapshot_sequence=1,
        request_id=batch.request_id,
        batch_id=batch.batch_id,
        representation_revision=batch.manifest.representation_revision,
        occurrence_ids=batch.occurrence_ids,
        authority_source_id=AuthoritySourceId("authority-test"),
    )


def _binding(batch: AdmissionBatch) -> RepresentationBindingWitness:
    bound_revision = batch.manifest.representation_revision
    return RepresentationBindingWitness(
        counted_representation_revision=bound_revision,
        dispatched_representation_revision=bound_revision,
        final_manifest_revision=bound_revision,
        request_id=batch.request_id,
        batch_id=batch.batch_id,
        authority_source_id=AuthoritySourceId("authority-test"),
    )


def _snapshot(
    *,
    protocol_version: int = CONTEXT_ADMISSION_PROTOCOL_VERSION,
    model_identity: ModelIdentity | None = None,
) -> ContextWindowSnapshot:
    return ContextWindowSnapshot(
        protocol_version=protocol_version,
        window_epoch_id=WindowEpochId("epoch-1"),
        window_epoch_number=1,
        model_identity=model_identity or ModelIdentity.anthropic("claude-test"),
        tokenizer_identity=TokenizerIdentity("tokenizer-test"),
        snapshot_sequence=1,
        active_count=60,
        hard_limit=100,
        remaining_count=40,
    )


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


@pytest.mark.parametrize(("enum_type", "expected"), EXPECTED_ENUM_MEMBERS.items())
def test_protocol_v1_enum_members_are_exact(
    enum_type: type[object], expected: tuple[str, ...]
) -> None:
    assert tuple(enum_type.__members__) == expected
    assert len(tuple(enum_type)) == len(enum_type.__members__)


def test_producer_surface_is_the_independently_frozen_literal_set() -> None:
    assert tuple(ProducerSurface.__members__) == EXPECTED_PRODUCER_SURFACES


def test_event_and_effect_unions_are_closed() -> None:
    assert {event_type.__name__ for event_type in get_args(ContextAdmissionEvent)} == (
        EXPECTED_EVENT_TYPES
    )
    assert {effect_type.__name__ for effect_type in get_args(AdmissionEffect)} == (
        EXPECTED_EFFECT_TYPES
    )
    assert set(get_args(ContextAdmissionState)) == {
        UninitializedContextAdmissionState,
        ActiveContextAdmissionState,
    }


@pytest.mark.parametrize(
    ("record_type", "expected_fields"),
    [
        (
            UninitializedContextAdmissionState,
            (
                "protocol_version",
                "aggregate_revision",
                "admission_sequence",
                "processed_events",
                "idempotency_records",
                "expired_idempotency_tombstones",
                "closed_epochs",
            ),
        ),
        (
            ActiveContextAdmissionState,
            (
                "protocol_version",
                "aggregate_revision",
                "admission_sequence",
                "snapshot",
                "protected_pools",
                "occurrence_records",
                "batch_records",
                "reservations",
                "generation_reservations",
                "processed_events",
                "idempotency_records",
                "expired_idempotency_tombstones",
                "closed_epochs",
            ),
        ),
        (
            AdmissionDecision,
            (
                "kind",
                "reason_code",
                "window_epoch_id",
                "snapshot_sequence",
                "requested_count",
                "available_ordinary_count",
                "available_protected_count",
            ),
        ),
        (AdmissionTransition, ("next_state", "decision", "effects")),
    ],
)
def test_frozen_record_fields_are_exact(
    record_type: type[object], expected_fields: tuple[str, ...]
) -> None:
    assert _field_names(record_type) == expected_fields


def test_lineage_fields_keep_all_identity_domains_separate() -> None:
    assert _field_names(ContextLineage) == (
        "root_session_id",
        "current_session_id",
        "root_agent_id",
        "current_agent_id",
        "parent_agent_id",
        "root_thread_id",
        "current_thread_id",
        "parent_thread_id",
        "fork_occurrence_id",
        "turn_id",
        "producer_surface",
        "producer_instance_id",
        "tool_call_id",
        "model_item_id",
        "dispatch_identity",
        "attempt_id",
        "delivery_occurrence_id",
        "window_epoch_id",
        "window_epoch_number",
    )


@pytest.mark.parametrize("opaque_type", OPAQUE_STRING_TYPES)
def test_opaque_string_identifiers_are_frozen_content_free_values(
    opaque_type: type[object],
) -> None:
    value = opaque_type("opaque-123")
    assert value.value == "opaque-123"
    assert value.from_dict(value.to_dict()) == value
    with pytest.raises((FrozenInstanceError, AttributeError)):
        value.value = "changed"


@pytest.mark.parametrize("revision_type", [AggregateRevision, AdmissionSequence])
def test_numeric_revisions_are_non_negative_and_have_no_default(
    revision_type: type[object],
) -> None:
    assert revision_type(0).value == 0
    with pytest.raises(ContextAdmissionValidationError):
        revision_type(-1)
    assert all(field.default is dataclasses.MISSING for field in fields(revision_type))


def test_dispatch_identity_is_validated_and_projects_only_dispatch_id() -> None:
    identity = DispatchIdentity.from_dispatch_id("dispatch-12345678")
    lineage = _lineage(surface=ProducerSurface.NATIVE_SHELL, dispatch_identity=identity)
    serialized = lineage.to_dict()
    assert serialized["dispatch_identity"] == {"dispatch_id": identity.dispatch_id}
    assert ContextLineage.from_dict(serialized) == lineage

    forged = replace(identity, completion_marker="forged-private-marker")
    with pytest.raises(ContextAdmissionValidationError) as exc_info:
        replace(lineage, dispatch_identity=forged)
    assert "forged-private-marker" not in str(exc_info.value)


def test_authoritative_snapshot_reuses_only_known_model_identity() -> None:
    snapshot = _snapshot()
    assert ContextWindowSnapshot.from_dict(snapshot.to_dict()) == snapshot
    assert snapshot.model_identity == ModelIdentity.anthropic("claude-test")
    with pytest.raises(ContextAdmissionValidationError):
        _snapshot(model_identity=ModelIdentity.unknown())


def test_uninitialized_state_is_explicitly_non_spendable() -> None:
    state = _uninitialized()
    assert not isinstance(state, ActiveContextAdmissionState)
    assert not hasattr(state, "snapshot")
    assert not hasattr(state, "reservations")
    assert UninitializedContextAdmissionState.from_dict(state.to_dict()) == state


def test_reservation_key_is_attempt_independent_and_revision_sensitive() -> None:
    key = AdmissionReservationKey(
        idempotency_namespace=IdempotencyNamespace(
            caller_scope="caller-1", operation_kind="reserve-request"
        ),
        protocol_version=CONTEXT_ADMISSION_PROTOCOL_VERSION,
        window_epoch_id=WindowEpochId("epoch-1"),
        window_epoch_number=1,
        batch_id=AdmissionBatchId("batch-1"),
        reserve_class=ReserveClass.ORDINARY,
        protected_pool_owner_id=None,
        occurrence_revisions=(
            (
                AdmissionOccurrenceId("occurrence-1"),
                RepresentationRevision("representation-1"),
            ),
        ),
    )
    assert "attempt" not in _field_names(AdmissionReservationKey)
    assert key.from_dict(key.to_dict()) == key
    assert (
        replace(
            key,
            occurrence_revisions=(
                (
                    AdmissionOccurrenceId("occurrence-1"),
                    RepresentationRevision("representation-2"),
                ),
            ),
        )
        != key
    )


def test_protected_pool_policy_is_injected_and_has_no_borrowing_or_defaults() -> None:
    assert _field_names(ProtectedPoolSpec) == (
        "reserve_class",
        "capability_owner_id",
        "injected_count",
        "priority",
        "required_release_witness_kind",
    )
    assert "borrow" not in " ".join(_field_names(ProtectedPoolSpec))
    assert all(field.default is dataclasses.MISSING for field in fields(ProtectedPoolSpec))
    with pytest.raises(ContextAdmissionValidationError):
        ProtectedPoolSpec(
            reserve_class=ReserveClass.ORDINARY,
            capability_owner_id=ProtectedPoolOwnerId("ordinary-must-not-own-a-pool"),
            injected_count=1,
            priority=1,
            required_release_witness_kind=WitnessKind.NON_ADMISSION,
        )


def test_manifest_rejects_overlapping_span_ownership() -> None:
    tool_argument = _occurrence(
        "tool-argument",
        span_id="shared-span",
        surface=ProducerSurface.TOOL_ARGUMENT,
    )
    assistant_history = _occurrence(
        "assistant-history",
        span_id="shared-span",
        surface=ProducerSurface.ASSISTANT_OUTPUT_HISTORY,
    )
    with pytest.raises(ContextAdmissionValidationError):
        _manifest(tool_argument, assistant_history)


def test_contract_values_are_deeply_immutable() -> None:
    occurrence = _occurrence()
    assert isinstance(occurrence.owned_span_ids, tuple)
    with pytest.raises((FrozenInstanceError, AttributeError)):
        occurrence.predicted_authoritative_maximum = 100

    def assert_no_mutable_collections(value: object) -> None:
        if dataclasses.is_dataclass(value):
            for field in fields(value):
                assert_no_mutable_collections(getattr(value, field.name))
        elif isinstance(value, tuple | frozenset):
            for child in value:
                assert_no_mutable_collections(child)
        else:
            assert not isinstance(value, list | dict | set)

    assert_no_mutable_collections(_manifest(occurrence))


@pytest.mark.parametrize(
    "canary",
    [
        "payload: private user message",
        "/home/alice/private/context.txt",
        "Bearer secret-token-value",
        f"sha256:{'a' * 64}",
    ],
)
def test_privacy_canaries_never_escape_validation_repr_or_serialization(canary: str) -> None:
    with pytest.raises(ContextAdmissionValidationError) as exc_info:
        ContextSessionId(canary)
    assert canary not in str(exc_info.value)
    assert canary not in repr(exc_info.value)

    safe = _lineage()
    rendered = repr(safe)
    serialized = repr(safe.to_dict())
    assert canary not in rendered
    assert canary not in serialized


def test_unknown_protocol_versions_fail_closed() -> None:
    assert CONTEXT_ADMISSION_PROTOCOL_VERSION == 1
    with pytest.raises(UnsupportedContextAdmissionProtocolError):
        replace(_uninitialized(), protocol_version=2)
    with pytest.raises(UnsupportedContextAdmissionProtocolError):
        _snapshot(protocol_version=2)


def test_closed_epoch_audits_survive_state_serialization() -> None:
    empty_audit = ClosedEpochAudit(
        snapshot=_snapshot(),
        terminal_occurrence_records=(),
        terminal_reservations=(),
        closure_witness_id=AdmissionWitnessId("closure-witness-1"),
        fence_proof=None,
        processed_event_tombstones=(),
        retained_unresolved_count=0,
    )
    state = replace(_uninitialized(), closed_epochs=(empty_audit,))
    restored = UninitializedContextAdmissionState.from_dict(state.to_dict())
    assert restored == state
    assert restored.closed_epochs == (empty_audit,)


@pytest.mark.parametrize(
    "non_dispatch_surface",
    [
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
    ],
)
def test_dispatch_identity_is_rejected_on_non_dispatch_surfaces(
    non_dispatch_surface: ProducerSurface,
) -> None:
    identity = DispatchIdentity.from_dispatch_id("dispatch-12345678")
    with pytest.raises(ContextAdmissionValidationError) as exc_info:
        _lineage(surface=non_dispatch_surface, dispatch_identity=identity)
    assert "dispatch_identity_on_non_dispatch_surface" in str(exc_info.value)


def test_occurrence_record_rejects_duplicate_witness_ids() -> None:
    occurrence = _occurrence()
    with pytest.raises(ContextAdmissionValidationError) as exc_info:
        AdmissionOccurrenceRecord(
            occurrence=occurrence,
            state=AdmissionState.COMMITTED,
            batch_id=None,
            reservation_id=None,
            accepted_witness_ids=(
                AdmissionWitnessId("dup"),
                AdmissionWitnessId("dup"),
            ),
            indeterminate_reason_code=None,
            quarantine_reason_code=None,
        )
    assert "duplicate_witness_id" in str(exc_info.value)


def test_batch_record_rejects_committed_and_unresolved_simultaneously() -> None:
    occurrence = _occurrence()
    batch = _batch("batch-simul", (occurrence,))
    with pytest.raises(ContextAdmissionValidationError) as exc_info:
        AdmissionBatchRecord(
            batch=batch,
            state=AdmissionState.INDETERMINATE,
            reservation_id=None,
            witness_ids=(),
            prepared_input_count=None,
            committed_input_count=5,
            unresolved_input_count=5,
        )
    assert "committed_and_unresolved_simultaneously" in str(exc_info.value)


def test_privacy_canaries_are_rejected_from_coverage_evidence() -> None:
    with pytest.raises(ContextAdmissionValidationError):
        CoverageEvidence(
            claim_id="COV-test",
            kind=CoverageEvidenceKind.AUTOSKILLIT_SOURCE,
            backend="autoskillit",
            configuration_mode="default",
            verifier="source_inspection",
            source_locator="/home/alice/private/source.py",
            tested_version="0.10.890",
            tested_revision="ac8f653a00d2",
            checked_at="2026-07-23",
            freshness_policy="verify_on_version_or_configuration_change",
        )
    with pytest.raises(ContextAdmissionValidationError):
        CoverageEvidence(
            claim_id="COV-test",
            kind=CoverageEvidenceKind.AUTOSKILLIT_SOURCE,
            backend="autoskillit",
            configuration_mode="default",
            verifier="source_inspection",
            source_locator="~alice/private",
            tested_version="0.10.890",
            tested_revision="ac8f653a00d2",
            checked_at="2026-07-23",
            freshness_policy="verify_on_version_or_configuration_change",
        )


def test_prepare_event_rejects_estimate_measurement() -> None:
    occurrence = _occurrence()
    batch = _batch("batch-estimate", (occurrence,))
    with pytest.raises(ContextAdmissionValidationError) as exc_info:
        PrepareBatchEvent(
            event_id=AdmissionEventId("prepare-estimate"),
            protocol_version=CONTEXT_ADMISSION_PROTOCOL_VERSION,
            idempotency_namespace=_namespace("prepare-batch"),
            expected_aggregate_revision=AggregateRevision(0),
            batch_id=batch.batch_id,
            representation_revision=batch.manifest.representation_revision,
            proposed_charge=5,
            measurement_kind=MeasurementKind.HOST_ESTIMATE,
            authority_source_id=AuthoritySourceId("authority-test"),
        )
    assert "non_authoritative_measurement" in str(exc_info.value)


def test_accept_event_rejects_estimate_measurement() -> None:
    occurrence = _occurrence()
    batch = _batch("batch-estimate-accept", (occurrence,))
    with pytest.raises(ContextAdmissionValidationError) as exc_info:
        AcceptInputEvent(
            event_id=AdmissionEventId("accept-estimate"),
            protocol_version=CONTEXT_ADMISSION_PROTOCOL_VERSION,
            idempotency_namespace=_namespace("accept-input"),
            expected_aggregate_revision=AggregateRevision(0),
            batch_id=batch.batch_id,
            witness=_witness(batch, WitnessKind.PROVIDER_ACCEPTED),
            final_manifest_revision=batch.manifest.representation_revision,
            exact_input_charge=5,
            measurement_kind=MeasurementKind.BYTE_EMERGENCY,
            authority_source_id=AuthoritySourceId("authority-test"),
            representation_binding_witness=_binding(batch),
        )
    assert "non_authoritative_measurement" in str(exc_info.value)
