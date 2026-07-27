"""Persistence-facing context-admission contract tests."""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import replace
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
    ContextAdmissionStreamHealth,
    ContextAdmissionStreamKey,
    ContextAdmissionValidationError,
    ContextSessionId,
    ContextThreadId,
    CoverageState,
    ForkOccurrenceId,
    IdempotencyNamespace,
    ModelIdentity,
    ShadowContextAdmissionRecord,
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
from tests.fixtures.context_admission import snapshot

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
            transition=AdmissionTransition(
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
            ),
        )
    failed = ContextAdmissionAccountingResult(
        status=ContextAdmissionAccountingStatus.STORAGE_FAIL_CLOSED,
        stream_key=_stream_key(),
        failure_reason=ContextAdmissionStorageFailureReason.INTEGRITY,
        reason_code="integrity",
    )
    assert failed.transition is None


def test_reducer_registry_is_exact_and_rejects_unsupported_versions() -> None:
    assert tuple(CONTEXT_ADMISSION_REDUCER_REGISTRY) == (1,)
    reducer_def = context_admission_reducer_for_protocol(1)
    assert reducer_def.reduce_transition is reduce_context_admission
    with pytest.raises(UnsupportedContextAdmissionProtocolError):
        context_admission_reducer_for_protocol(2)
    with pytest.raises(TypeError):
        CONTEXT_ADMISSION_REDUCER_REGISTRY[2] = reducer_def  # type: ignore[index]


def test_envelope_constructor_rejects_discriminator_payload_mismatch() -> None:
    with pytest.raises(ContextAdmissionValidationError):
        StoredContextAdmissionEnvelope(
            encoding_version=CONTEXT_ADMISSION_ENCODING_VERSION,
            protocol_version=CONTEXT_ADMISSION_PROTOCOL_VERSION,
            type_discriminator="AdmissionDecision",
            payload=_authority_event(),
        )
