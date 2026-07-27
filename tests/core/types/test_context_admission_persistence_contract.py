"""Persistence-facing context-admission contract tests."""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import replace
from hashlib import sha256
from pathlib import Path
from types import MappingProxyType
from typing import cast, get_args

import pytest

import autoskillit.core.types._type_context_admission_persistence as persistence_types
from autoskillit.core import (
    CONTEXT_ADMISSION_ENCODING_VERSION,
    CONTEXT_ADMISSION_PROTOCOL_VERSION,
    CONTEXT_ADMISSION_REDUCER_REGISTRY,
    CONTEXT_ADMISSION_TOP_LEVEL_DISCRIMINATORS,
    AdmissionDecision,
    AdmissionDecisionKind,
    AdmissionEffect,
    AdmissionEventId,
    AdmissionSequence,
    AdmissionState,
    AdmissionTransition,
    AgentInstanceId,
    AggregateRevision,
    AuthorityUnavailableEvent,
    ContextAdmissionAccountingResult,
    ContextAdmissionAccountingStatus,
    ContextAdmissionEvent,
    ContextAdmissionInspectionResult,
    ContextAdmissionState,
    ContextAdmissionStorageFailureReason,
    ContextAdmissionStorageHealthStatus,
    ContextAdmissionStoreAuthority,
    ContextAdmissionStoreHealth,
    ContextAdmissionStreamHealth,
    ContextAdmissionStreamKey,
    ContextAdmissionValidationError,
    ContextSessionId,
    ContextThreadId,
    CoverageState,
    ForkOccurrenceId,
    IdempotencyNamespace,
    MeasurementKind,
    ModelIdentity,
    ShadowContextAdmissionRecord,
    ShadowContextAdmissionTargetRecord,
    StoredContextAdmissionEnvelope,
    UninitializedContextAdmissionState,
    UnsupportedContextAdmissionProtocolError,
    context_admission_envelope_header,
    context_admission_reducer_for_protocol,
    decode_stored_context_admission_envelope,
    encode_stored_context_admission_envelope,
    make_stored_context_admission_envelope,
    reduce_context_admission,
    validate_context_admission_persistence_value,
)
from tests.fixtures.context_admission import (
    batch,
    occurrence,
    open_event,
    propose_event,
    reservation,
    reserve_event,
    snapshot,
    stream_key,
    uninitialized_state,
)

pytestmark = [pytest.mark.layer("core"), pytest.mark.medium]

_GOLDEN_JOURNAL = (
    Path(__file__).parents[2]
    / "fixtures"
    / "context_admission_journals"
    / "protocol_v1_encoding_v1.json"
)


def _stream_key() -> ContextAdmissionStreamKey:
    return ContextAdmissionStreamKey(
        root_session_id=ContextSessionId("session-root"),
        current_session_id=ContextSessionId("session-current"),
        root_agent_id=AgentInstanceId("agent-root"),
        current_agent_id=AgentInstanceId("agent-current"),
        root_thread_id=ContextThreadId("thread-root"),
        current_thread_id=ContextThreadId("thread-current"),
        fork_occurrence_id=ForkOccurrenceId("fork-one"),
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


def _authority_event() -> AuthorityUnavailableEvent:
    return AuthorityUnavailableEvent(
        event_id=AdmissionEventId("event-authority"),
        protocol_version=CONTEXT_ADMISSION_PROTOCOL_VERSION,
        idempotency_namespace=IdempotencyNamespace(
            caller_scope="ledger-test",
            operation_kind="authority-unavailable",
        ),
        expected_aggregate_revision=AggregateRevision(0),
        reason_code="watermark-unavailable",
        authority_state=CoverageState.PARTIAL,
    )


def _transition() -> AdmissionTransition:
    return AdmissionTransition(
        next_state=_uninitialized(),
        decision=AdmissionDecision(
            kind=AdmissionDecisionKind.WOULD_ADMIT,
            reason_code="would-admit",
            window_epoch_id=None,
            snapshot_sequence=None,
            requested_count=0,
            available_ordinary_count=0,
            available_protected_count=0,
        ),
        effects=(),
    )


@pytest.mark.parametrize("owner_id", [True, -1, 1000.0, "1000"])
def test_store_authority_rejects_invalid_owner_ids(
    tmp_path: Path,
    owner_id: object,
) -> None:
    with pytest.raises(ValueError, match="invalid_context_admission_store_owner"):
        ContextAdmissionStoreAuthority(
            database_path=(tmp_path / "ledger.sqlite3").resolve(),
            expected_owner_id=owner_id,  # type: ignore[arg-type]
        )


def test_stream_key_round_trips_and_partitions_every_lineage_coordinate() -> None:
    stream_key = _stream_key()
    assert ContextAdmissionStreamKey.from_dict(stream_key.to_dict()) == stream_key

    replacements = {
        "root_session_id": ContextSessionId("session-root-two"),
        "current_session_id": ContextSessionId("session-current-two"),
        "root_agent_id": AgentInstanceId("agent-root-two"),
        "current_agent_id": AgentInstanceId("agent-current-two"),
        "root_thread_id": ContextThreadId("thread-root-two"),
        "current_thread_id": ContextThreadId("thread-current-two"),
        "fork_occurrence_id": ForkOccurrenceId("fork-two"),
    }
    assert all(
        replace(stream_key, **{name: value}) != stream_key for name, value in replacements.items()
    )


@pytest.mark.parametrize(
    "payload",
    [
        _authority_event(),
        _uninitialized(),
        AdmissionDecision(
            kind=AdmissionDecisionKind.WATERMARK_UNAVAILABLE,
            reason_code="watermark-unavailable",
            window_epoch_id=None,
            snapshot_sequence=None,
            requested_count=0,
            available_ordinary_count=0,
            available_protected_count=0,
        ),
    ],
)
def test_released_envelopes_have_byte_stable_canonical_round_trip(
    payload: object,
) -> None:
    envelope = make_stored_context_admission_envelope(payload)  # type: ignore[arg-type]
    encoded = encode_stored_context_admission_envelope(envelope)
    assert encoded == encode_stored_context_admission_envelope(envelope)
    assert decode_stored_context_admission_envelope(encoded) == envelope
    assert b" " not in encoded


def test_envelope_encoder_rejects_output_above_decoder_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    envelope = make_stored_context_admission_envelope(_authority_event())
    encoded = encode_stored_context_admission_envelope(envelope)
    monkeypatch.setattr(persistence_types, "_MAX_ENVELOPE_BYTES", len(encoded) - 1)

    with pytest.raises(
        ContextAdmissionValidationError, match="invalid_context_admission_envelope"
    ):
        encode_stored_context_admission_envelope(envelope)


def test_active_state_effect_and_populated_shadow_have_exact_encoding_vectors() -> None:
    initial = uninitialized_state()
    opened = reduce_context_admission(initial, open_event(initial))
    active = opened.next_state
    occurrence_value = occurrence()
    proposed_event = propose_event(active, occurrence_value)
    proposed = reduce_context_admission(active, proposed_event)
    batch_value = batch(occurrence_value)
    reservation_value = reservation(batch_value, occurrence_value)
    reserved_event = reserve_event(proposed.next_state, batch_value, occurrence_value)
    reserved = reduce_context_admission(proposed.next_state, reserved_event)
    target = ShadowContextAdmissionTargetRecord(
        target_id=batch_value.batch_id,
        occurrence_ids=batch_value.occurrence_ids,
        turn_ids=(occurrence_value.lineage.turn_id,),
        tool_call_ids=(occurrence_value.lineage.tool_call_id,),
        producer_instance_ids=(occurrence_value.lineage.producer_instance_id,),
        producer_surfaces=(occurrence_value.lineage.producer_surface,),
        delivery_occurrence_ids=(occurrence_value.lineage.delivery_occurrence_id,),
        reservation_id=reservation_value.reservation_id,
        batch_id=batch_value.batch_id,
        generation_reservation_id=None,
        window_epoch_id=occurrence_value.lineage.window_epoch_id,
        reserve_class=batch_value.reserve_class,
        lifecycle_state=AdmissionState.RESERVED,
        proposed_input_count=occurrence_value.predicted_authoritative_maximum,
        generation_allowance=None,
        exact_input_charge=None,
        exact_output_charge=None,
        measurement_kind=MeasurementKind.TOKENIZER_EXACT,
    )
    shadow = ShadowContextAdmissionRecord(
        stream_key=stream_key(),
        event_id=reserved_event.event_id,
        journal_sequence=3,
        aggregate_revision=reserved.next_state.aggregate_revision,
        admission_sequence=reserved.next_state.admission_sequence,
        decision=reserved.decision,
        protocol_version=CONTEXT_ADMISSION_PROTOCOL_VERSION,
        encoding_version=CONTEXT_ADMISSION_ENCODING_VERSION,
        reason_code=reserved.decision.reason_code,
        targets=(target,),
    )
    expected_digests = {
        "active_state": "fda08ea2f4c7103ac14663d6b616c13a380efca2325c7a9afaefc45d80eb6ee0",
        "effect": "ba5827153bc035734784c9aa9cafa1e45ba27b049dbb3e52da7c2eb71680a60d",
        "populated_shadow": "0765b580fbf148dc2ea043da90af48f0378593aa7d0d4ae2cfe96008d09d5127",
    }

    for name, payload in (
        ("active_state", active),
        ("effect", reserved.effects[0]),
        ("populated_shadow", shadow),
    ):
        encoded = encode_stored_context_admission_envelope(
            make_stored_context_admission_envelope(payload)
        )
        assert sha256(encoded).hexdigest() == expected_digests[name]


def test_shadow_publication_is_a_released_top_level_envelope() -> None:
    transition = reduce_context_admission(_uninitialized(), _authority_event())
    shadow = ShadowContextAdmissionRecord(
        stream_key=_stream_key(),
        event_id=_authority_event().event_id,
        journal_sequence=1,
        aggregate_revision=transition.next_state.aggregate_revision,
        admission_sequence=transition.next_state.admission_sequence,
        decision=transition.decision,
        protocol_version=CONTEXT_ADMISSION_PROTOCOL_VERSION,
        encoding_version=CONTEXT_ADMISSION_ENCODING_VERSION,
        reason_code=transition.decision.reason_code,
        targets=(),
    )
    envelope = make_stored_context_admission_envelope(shadow)
    assert (
        decode_stored_context_admission_envelope(
            encode_stored_context_admission_envelope(envelope)
        ).payload
        == shadow
    )
    assert "ShadowContextAdmissionRecord" in CONTEXT_ADMISSION_TOP_LEVEL_DISCRIMINATORS


def test_top_level_discriminator_allowlist_matches_released_unions_exactly() -> None:
    expected = {
        value_type.__name__
        for value_type in (
            *get_args(ContextAdmissionEvent),
            *get_args(AdmissionEffect),
            *get_args(ContextAdmissionState),
            AdmissionDecision,
            ShadowContextAdmissionRecord,
        )
    }
    assert CONTEXT_ADMISSION_TOP_LEVEL_DISCRIMINATORS == expected


def test_protocol_v1_golden_journal_replays_byte_identically() -> None:
    fixture = json.loads(_GOLDEN_JOURNAL.read_text(encoding="utf-8"))
    assert fixture["schema_version"] == 1
    assert fixture["encoding_version"] == CONTEXT_ADMISSION_ENCODING_VERSION
    assert fixture["protocol_version"] == CONTEXT_ADMISSION_PROTOCOL_VERSION
    assert ContextAdmissionStreamKey.from_dict(fixture["stream_key"]) == ContextAdmissionStreamKey(
        root_session_id=ContextSessionId("session-root"),
        current_session_id=ContextSessionId("session-root"),
        root_agent_id=AgentInstanceId("agent-root"),
        current_agent_id=AgentInstanceId("agent-root"),
        root_thread_id=ContextThreadId("thread-root"),
        current_thread_id=ContextThreadId("thread-root"),
        fork_occurrence_id=None,
    )

    reducer = context_admission_reducer_for_protocol(CONTEXT_ADMISSION_PROTOCOL_VERSION)
    replayed_states = []
    for _ in range(2):
        state: ContextAdmissionState = _uninitialized()
        for expected_sequence, publication in enumerate(fixture["publications"], start=1):
            assert publication["journal_sequence"] == expected_sequence
            event_envelope = decode_stored_context_admission_envelope(
                publication["event_envelope"].encode("utf-8")
            )
            decision_envelope = decode_stored_context_admission_envelope(
                publication["decision_envelope"].encode("utf-8")
            )
            shadow_envelope = decode_stored_context_admission_envelope(
                publication["shadow_envelope"].encode("utf-8")
            )
            assert (
                encode_stored_context_admission_envelope(event_envelope).decode("utf-8")
                == publication["event_envelope"]
            )
            assert (
                encode_stored_context_admission_envelope(decision_envelope).decode("utf-8")
                == publication["decision_envelope"]
            )
            assert (
                encode_stored_context_admission_envelope(shadow_envelope).decode("utf-8")
                == publication["shadow_envelope"]
            )

            transition = reducer.reduce_transition(
                state,
                cast(ContextAdmissionEvent, event_envelope.payload),
            )
            assert transition.decision == decision_envelope.payload
            stored_effects = tuple(
                decode_stored_context_admission_envelope(
                    effect["envelope"].encode("utf-8")
                ).payload
                for effect in publication["effect_envelopes"]
            )
            assert transition.effects == stored_effects
            assert isinstance(shadow_envelope.payload, ShadowContextAdmissionRecord)
            assert shadow_envelope.payload.journal_sequence == expected_sequence
            assert shadow_envelope.payload.decision == transition.decision
            state = transition.next_state

        final_envelope = decode_stored_context_admission_envelope(
            fixture["final_state_envelope"].encode("utf-8")
        )
        assert final_envelope.payload == state
        assert (
            encode_stored_context_admission_envelope(final_envelope).decode("utf-8")
            == fixture["final_state_envelope"]
        )
        replayed_states.append(state)

    assert replayed_states[0] == replayed_states[1]


def test_envelope_rejects_unknown_or_noncanonical_routes() -> None:
    encoded = encode_stored_context_admission_envelope(
        make_stored_context_admission_envelope(_authority_event())
    )
    with pytest.raises(ContextAdmissionValidationError):
        decode_stored_context_admission_envelope(encoded.replace(b"Authority", b"Unknown"))
    with pytest.raises(ContextAdmissionValidationError):
        decode_stored_context_admission_envelope(b'{"payload":{},"encoding_version":1}')
    with pytest.raises(ContextAdmissionValidationError):
        decode_stored_context_admission_envelope(b" " + encoded)


@pytest.mark.parametrize(
    "decoder",
    [
        decode_stored_context_admission_envelope,
        context_admission_envelope_header,
    ],
)
def test_envelope_decoders_reject_unbounded_json_before_parsing(
    decoder: object,
) -> None:
    deeply_nested = (
        b'{"encoding_version":1,"payload":'
        + (b"[" * 129)
        + b"null"
        + (b"]" * 129)
        + b',"protocol_version":1,"type_discriminator":"AuthorityUnavailableEvent"}'
    )
    with pytest.raises(ContextAdmissionValidationError):
        cast(Callable[[bytes], object], decoder)(deeply_nested)

    with pytest.raises(ContextAdmissionValidationError):
        cast(Callable[[bytes], object], decoder)(b" " * (16 * 1024 * 1024 + 1))


def test_envelope_decoder_consumes_deterministic_upcaster_route(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    encoded = encode_stored_context_admission_envelope(
        make_stored_context_admission_envelope(_authority_event())
    )
    legacy = encoded.replace(b'"encoding_version":1', b'"encoding_version":0', 1)

    monkeypatch.setattr(
        persistence_types,
        "CONTEXT_ADMISSION_ENVELOPE_UPCASTERS",
        MappingProxyType(
            {
                (0, 1): lambda value: value.replace(
                    b'"encoding_version":0',
                    b'"encoding_version":1',
                    1,
                )
            }
        ),
    )

    decoded = decode_stored_context_admission_envelope(legacy)

    assert decoded.encoding_version == CONTEXT_ADMISSION_ENCODING_VERSION
    assert decoded.payload == _authority_event()


def _legacy_envelope_bytes() -> bytes:
    encoded = encode_stored_context_admission_envelope(
        make_stored_context_admission_envelope(_authority_event())
    )
    return encoded.replace(b'"encoding_version":1', b'"encoding_version":0', 1)


@pytest.mark.parametrize(
    "routes",
    [
        {},
        {
            (0, 1): lambda value: value,
            (0, 2): lambda value: value,
        },
    ],
)
def test_envelope_decoder_rejects_missing_or_ambiguous_upcaster_routes(
    monkeypatch: pytest.MonkeyPatch,
    routes: dict[tuple[int, int], Callable[[bytes], bytes]],
) -> None:
    monkeypatch.setattr(
        persistence_types,
        "CONTEXT_ADMISSION_ENVELOPE_UPCASTERS",
        MappingProxyType(routes),
    )

    with pytest.raises(ContextAdmissionValidationError, match="unsupported"):
        decode_stored_context_admission_envelope(_legacy_envelope_bytes())


@pytest.mark.parametrize("target_version", [0, 2])
def test_envelope_decoder_rejects_invalid_upcaster_targets(
    monkeypatch: pytest.MonkeyPatch,
    target_version: int,
) -> None:
    monkeypatch.setattr(
        persistence_types,
        "CONTEXT_ADMISSION_ENVELOPE_UPCASTERS",
        MappingProxyType({(0, target_version): lambda value: value}),
    )

    with pytest.raises(ContextAdmissionValidationError, match="ambiguous"):
        decode_stored_context_admission_envelope(_legacy_envelope_bytes())


def test_envelope_decoder_rejects_nonbytes_upcaster_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    invalid_upcaster = cast(Callable[[bytes], bytes], lambda _value: "not-bytes")
    monkeypatch.setattr(
        persistence_types,
        "CONTEXT_ADMISSION_ENVELOPE_UPCASTERS",
        MappingProxyType({(0, 1): invalid_upcaster}),
    )

    with pytest.raises(ContextAdmissionValidationError, match="invalid_context_admission_upcast"):
        decode_stored_context_admission_envelope(_legacy_envelope_bytes())


@pytest.mark.parametrize(
    ("header_name", "new_header"),
    [
        ("protocol_version", 2),
        ("type_discriminator", "AdmissionDecision"),
    ],
)
def test_envelope_decoder_rejects_upcaster_header_changes(
    monkeypatch: pytest.MonkeyPatch,
    header_name: str,
    new_header: object,
) -> None:
    def alter_header(value: bytes) -> bytes:
        envelope = json.loads(value)
        envelope["encoding_version"] = 1
        envelope[header_name] = new_header
        return json.dumps(envelope, separators=(",", ":"), sort_keys=True).encode()

    monkeypatch.setattr(
        persistence_types,
        "CONTEXT_ADMISSION_ENVELOPE_UPCASTERS",
        MappingProxyType({(0, 1): alter_header}),
    )

    with pytest.raises(ContextAdmissionValidationError, match="invalid_context_admission_upcast"):
        decode_stored_context_admission_envelope(_legacy_envelope_bytes())


@pytest.mark.parametrize(
    "value",
    [
        "/private/store.sqlite3",
        "~/ledger",
        "password=visible",
        "a" * 64,
        {"nested": "ghp_visible"},
        replace(
            snapshot(),
            model_identity=ModelIdentity.anthropic("ghp_visible"),
        ),
    ],
)
def test_recursive_persistence_boundary_rejects_sensitive_values(
    value: object,
) -> None:
    with pytest.raises(ContextAdmissionValidationError):
        validate_context_admission_persistence_value(value)


def test_recursive_persistence_boundary_rejects_mutable_mappings() -> None:
    with pytest.raises(ContextAdmissionValidationError, match="invalid_persistence_value"):
        validate_context_admission_persistence_value({"benign": 1})


def test_inspection_result_rejects_mismatched_stream_health_identity() -> None:
    key = _stream_key()
    mismatched_key = replace(
        key,
        current_thread_id=ContextThreadId("thread-other"),
    )
    with pytest.raises(ValueError, match="inspection_stream_health_identity_mismatch"):
        ContextAdmissionInspectionResult(
            stream_key=key,
            health=ContextAdmissionStreamHealth(
                mismatched_key,
                ContextAdmissionStorageHealthStatus.HEALTHY,
            ),
            state=None,
            events=(),
            decisions=(),
            effects=(),
            shadows=(),
            latest_journal_sequence=0,
        )


def test_accounting_results_enforce_nonadmitting_storage_outcomes() -> None:
    with pytest.raises(ValueError, match="nonadmitting"):
        ContextAdmissionAccountingResult(
            status=ContextAdmissionAccountingStatus.CONTENDED,
            stream_key=_stream_key(),
            transition=_transition(),
        )
    failed = ContextAdmissionAccountingResult(
        status=ContextAdmissionAccountingStatus.STORAGE_FAIL_CLOSED,
        stream_key=_stream_key(),
        failure_reason=ContextAdmissionStorageFailureReason.INTEGRITY,
        reason_code="integrity",
    )
    assert failed.transition is None


def test_health_and_accounting_results_reject_raw_enum_values() -> None:
    with pytest.raises(ValueError, match="invalid_context_admission_storage_health"):
        ContextAdmissionStoreHealth(
            status="healthy",  # type: ignore[arg-type]
        )
    with pytest.raises(ValueError, match="invalid_context_admission_accounting_status"):
        ContextAdmissionAccountingResult(
            status="recorded",  # type: ignore[arg-type]
            stream_key=_stream_key(),
        )
    with pytest.raises(
        ValueError,
        match="invalid_context_admission_storage_failure_reason",
    ):
        ContextAdmissionAccountingResult(
            status=ContextAdmissionAccountingStatus.STORAGE_FAIL_CLOSED,
            stream_key=_stream_key(),
            failure_reason="integrity",  # type: ignore[arg-type]
        )


@pytest.mark.parametrize(
    "status",
    [
        ContextAdmissionAccountingStatus.RECORDED,
        ContextAdmissionAccountingStatus.EXACT_REPLAY,
        ContextAdmissionAccountingStatus.RECONCILIATION_REQUIRED,
        ContextAdmissionAccountingStatus.PROTOCOL_QUARANTINED,
    ],
)
def test_published_accounting_results_require_transition_and_sequence(
    status: ContextAdmissionAccountingStatus,
) -> None:
    with pytest.raises(ValueError):
        ContextAdmissionAccountingResult(
            status=status,
            stream_key=_stream_key(),
        )
    with pytest.raises(ValueError):
        ContextAdmissionAccountingResult(
            status=status,
            stream_key=_stream_key(),
            transition=_transition(),
        )


def test_semantic_rejection_requires_transition() -> None:
    with pytest.raises(ValueError, match="semantic_rejection_requires_transition"):
        ContextAdmissionAccountingResult(
            status=ContextAdmissionAccountingStatus.SEMANTIC_REJECTION,
            stream_key=_stream_key(),
        )


def test_storage_failure_rejects_publication_and_nonstorage_failure_reason() -> None:
    with pytest.raises(ValueError, match="nonadmitting_storage_result_has_transition"):
        ContextAdmissionAccountingResult(
            status=ContextAdmissionAccountingStatus.STORAGE_FAIL_CLOSED,
            stream_key=_stream_key(),
            transition=_transition(),
            journal_sequence=1,
            failure_reason=ContextAdmissionStorageFailureReason.INTEGRITY,
        )
    with pytest.raises(ValueError, match="nonstorage_result_has_storage_reason"):
        ContextAdmissionAccountingResult(
            status=ContextAdmissionAccountingStatus.SEMANTIC_REJECTION,
            stream_key=_stream_key(),
            transition=_transition(),
            failure_reason=ContextAdmissionStorageFailureReason.INTEGRITY,
        )


def test_reducer_registry_is_exact_and_rejects_unsupported_versions() -> None:
    assert tuple(CONTEXT_ADMISSION_REDUCER_REGISTRY) == (1,)
    reducer_def = context_admission_reducer_for_protocol(1)
    assert reducer_def.protocol_version == tuple(CONTEXT_ADMISSION_REDUCER_REGISTRY)[0]
    assert reducer_def.reduce_transition is reduce_context_admission
    with pytest.raises(UnsupportedContextAdmissionProtocolError):
        context_admission_reducer_for_protocol(2)
    with pytest.raises(TypeError):
        CONTEXT_ADMISSION_REDUCER_REGISTRY[2] = reducer_def  # type: ignore[index]


@pytest.mark.parametrize("protocol_version", [True, 1.0, "1"])
def test_reducer_selector_rejects_non_integer_versions(protocol_version: object) -> None:
    with pytest.raises(UnsupportedContextAdmissionProtocolError):
        context_admission_reducer_for_protocol(protocol_version)  # type: ignore[arg-type]


def test_envelope_constructor_rejects_discriminator_payload_mismatch() -> None:
    with pytest.raises(ContextAdmissionValidationError):
        StoredContextAdmissionEnvelope(
            encoding_version=CONTEXT_ADMISSION_ENCODING_VERSION,
            protocol_version=CONTEXT_ADMISSION_PROTOCOL_VERSION,
            type_discriminator="AdmissionDecision",
            payload=_authority_event(),
        )


@pytest.mark.parametrize("encoding_version", [True, 1.0, "1"])
def test_envelope_constructor_rejects_non_integer_encoding_versions(
    encoding_version: object,
) -> None:
    with pytest.raises(
        ContextAdmissionValidationError,
        match="unsupported_context_admission_encoding",
    ):
        StoredContextAdmissionEnvelope(
            encoding_version=encoding_version,  # type: ignore[arg-type]
            protocol_version=CONTEXT_ADMISSION_PROTOCOL_VERSION,
            type_discriminator="AuthorityUnavailableEvent",
            payload=_authority_event(),
        )
