"""Stream projection replay and stored-health decoding.

Owns the replay-projection logic that walks the journal_events, effect_outbox,
and shadow_decisions tables to produce the recovered state, events, decisions,
effects, and shadows. Also owns the read-budget construction constants
(:data:`_MAX_RECOVERY_ROWS`, :data:`_MAX_RECOVERY_BYTES`) and the
``_stored_stream_health`` decoder.

Wavefront 1 of #4667.
"""

from __future__ import annotations

import sqlite3
from typing import Final, cast

from autoskillit.core import (
    AdmissionDecision,
    AdmissionEffect,
    AuthorityUnavailableEvent,
    ContextAdmissionEvent,
    ContextAdmissionState,
    ContextAdmissionStorageFailureReason,
    ContextAdmissionStorageHealthStatus,
    ContextAdmissionStreamHealth,
    ContextAdmissionStreamKey,
    ContextAdmissionValidationError,
    OpenEpochEvent,
    ShadowContextAdmissionRecord,
    UninitializedContextAdmissionState,
    context_admission_reducer_for_protocol,
    decode_stored_context_admission_envelope,
)

from ._codec import (
    _EFFECT_TYPES,
    _EVENT_TYPES,
    _STATE_TYPES,
    _zero_state,
)
from ._shadow import _shadow_record
from ._state_queries import _validate_event_stream_identity
from ._storage import (
    _LedgerOpenError,
    _LedgerReadBudget,
    _read_bounded_rows,
)

_MAX_RECOVERY_ROWS: Final = 100_000
_MAX_RECOVERY_BYTES: Final = 256 * 1024 * 1024

__all__ = [
    "_MAX_RECOVERY_ROWS",
    "_MAX_RECOVERY_BYTES",
    "_stored_stream_health",
    "_recover_stream_projection",
]


def _stored_stream_health(
    stream_key: ContextAdmissionStreamKey,
    status: object,
    failure_reason: object,
    reason_code: object,
    *,
    invalid_reason: ContextAdmissionStorageFailureReason = (
        ContextAdmissionStorageFailureReason.INTEGRITY
    ),
) -> ContextAdmissionStreamHealth:
    try:
        return ContextAdmissionStreamHealth(
            stream_key,
            ContextAdmissionStorageHealthStatus(str(status)),
            failure_reason=(
                ContextAdmissionStorageFailureReason(str(failure_reason))
                if failure_reason is not None
                else None
            ),
            reason_code=str(reason_code) if reason_code is not None else None,
        )
    except (ContextAdmissionValidationError, ValueError) as exc:
        raise _LedgerOpenError(
            invalid_reason,
            "invalid-stream-health",
        ) from exc


def _recover_stream_projection(
    connection: sqlite3.Connection,
    stream_id: bytes,
    stream_key: ContextAdmissionStreamKey,
    *,
    genesis_envelope: bytes,
    materialized_state_envelope: bytes,
    aggregate_revision: int,
    admission_sequence: int,
    latest_journal_sequence: int,
    read_budget: _LedgerReadBudget,
) -> tuple[
    ContextAdmissionState,
    tuple[ContextAdmissionEvent, ...],
    tuple[AdmissionDecision, ...],
    tuple[tuple[AdmissionEffect, ...], ...],
    tuple[ShadowContextAdmissionRecord, ...],
]:
    genesis_wrapper = decode_stored_context_admission_envelope(genesis_envelope)
    if not isinstance(genesis_wrapper.payload, UninitializedContextAdmissionState):
        raise _LedgerOpenError(
            ContextAdmissionStorageFailureReason.REPLAY_MISMATCH,
            "invalid-stream-genesis-type",
        )
    genesis = genesis_wrapper.payload
    if genesis_wrapper.protocol_version != genesis.protocol_version or genesis != _zero_state(
        genesis.protocol_version
    ):
        raise _LedgerOpenError(
            ContextAdmissionStorageFailureReason.REPLAY_MISMATCH,
            "invalid-stream-genesis",
        )
    if latest_journal_sequence <= 0:
        raise _LedgerOpenError(
            ContextAdmissionStorageFailureReason.AMBIGUOUS_RECOVERY,
            "empty-bound-stream",
        )
    journal_rows = _read_bounded_rows(
        connection.execute(
            """
            SELECT journal_sequence, event_id, event_envelope, decision_envelope,
                   expected_revision, prior_aggregate_revision,
                   prior_admission_sequence, resulting_aggregate_revision,
                   resulting_admission_sequence
            FROM journal_events
            WHERE stream_id = ?
            ORDER BY journal_sequence
            """,
            (stream_id,),
        ),
        read_budget,
    )
    sequences = tuple(int(row[0]) for row in journal_rows)
    if latest_journal_sequence != len(sequences) or any(
        sequence != expected for expected, sequence in enumerate(sequences, start=1)
    ):
        raise _LedgerOpenError(
            ContextAdmissionStorageFailureReason.REPLAY_MISMATCH,
            "journal-sequence-gap",
        )
    effects_by_sequence: dict[int, list[bytes]] = {sequence: [] for sequence in sequences}
    effect_rows = _read_bounded_rows(
        connection.execute(
            """
            SELECT journal_sequence, effect_ordinal, effect_envelope
            FROM effect_outbox
            WHERE stream_id = ?
            ORDER BY journal_sequence, effect_ordinal
            """,
            (stream_id,),
        ),
        read_budget,
    )
    for sequence, ordinal, envelope in effect_rows:
        effects = effects_by_sequence.get(int(sequence))
        if effects is None or int(ordinal) != len(effects):
            raise _LedgerOpenError(
                ContextAdmissionStorageFailureReason.REPLAY_MISMATCH,
                "effect-sequence-gap",
            )
        effects.append(bytes(envelope))
    shadow_rows = _read_bounded_rows(
        connection.execute(
            """
            SELECT journal_sequence, shadow_envelope
            FROM shadow_decisions
            WHERE stream_id = ?
            ORDER BY journal_sequence
            """,
            (stream_id,),
        ),
        read_budget,
    )
    if tuple(int(row[0]) for row in shadow_rows) != sequences:
        raise _LedgerOpenError(
            ContextAdmissionStorageFailureReason.REPLAY_MISMATCH,
            "shadow-sequence-gap",
        )
    shadow_by_sequence = {int(sequence): bytes(envelope) for sequence, envelope in shadow_rows}
    state: ContextAdmissionState = genesis
    events: list[ContextAdmissionEvent] = []
    replayed_decisions: list[AdmissionDecision] = []
    replayed_effects: list[tuple[AdmissionEffect, ...]] = []
    replayed_shadows: list[ShadowContextAdmissionRecord] = []
    for row in journal_rows:
        journal_sequence = int(row[0])
        event_wrapper = decode_stored_context_admission_envelope(bytes(row[2]))
        decision_wrapper = decode_stored_context_admission_envelope(bytes(row[3]))
        if not isinstance(event_wrapper.payload, _EVENT_TYPES) or not isinstance(
            decision_wrapper.payload,
            AdmissionDecision,
        ):
            raise ContextAdmissionValidationError("stored_publication_type_mismatch")
        event = cast(ContextAdmissionEvent, event_wrapper.payload)
        stored_decision = decision_wrapper.payload
        if str(row[1]) != event.event_id.value:
            raise _LedgerOpenError(
                ContextAdmissionStorageFailureReason.REPLAY_MISMATCH,
                "journal-event-identity-mismatch",
            )
        if journal_sequence == 1 and not isinstance(
            event,
            OpenEpochEvent | AuthorityUnavailableEvent,
        ):
            raise _LedgerOpenError(
                ContextAdmissionStorageFailureReason.REPLAY_MISMATCH,
                "invalid-initial-event",
            )
        protocol_version = event.protocol_version
        if (
            event_wrapper.protocol_version != protocol_version
            or decision_wrapper.protocol_version != protocol_version
            or state.protocol_version != protocol_version
        ):
            raise _LedgerOpenError(
                ContextAdmissionStorageFailureReason.REPLAY_MISMATCH,
                "publication-protocol-mismatch",
            )
        _validate_event_stream_identity(stream_key, event)
        if (
            int(row[4]) != event.expected_aggregate_revision.value
            or int(row[5]) != state.aggregate_revision.value
            or int(row[6]) != state.admission_sequence.value
        ):
            raise _LedgerOpenError(
                ContextAdmissionStorageFailureReason.REPLAY_MISMATCH,
                "journal-prior-coordinate-mismatch",
            )
        reducer = context_admission_reducer_for_protocol(protocol_version)
        transition = reducer.reduce_transition(state, event)
        events.append(event)
        if stored_decision != transition.decision:
            raise _LedgerOpenError(
                ContextAdmissionStorageFailureReason.REPLAY_MISMATCH,
                "journal-decision-mismatch",
            )
        replayed_decisions.append(stored_decision)
        stored_effects: list[AdmissionEffect] = []
        for encoded_effect in effects_by_sequence[journal_sequence]:
            effect_wrapper = decode_stored_context_admission_envelope(encoded_effect)
            if effect_wrapper.protocol_version != protocol_version or not isinstance(
                effect_wrapper.payload, _EFFECT_TYPES
            ):
                raise _LedgerOpenError(
                    ContextAdmissionStorageFailureReason.REPLAY_MISMATCH,
                    "effect-protocol-mismatch",
                )
            stored_effects.append(cast(AdmissionEffect, effect_wrapper.payload))
        if tuple(stored_effects) != transition.effects:
            raise _LedgerOpenError(
                ContextAdmissionStorageFailureReason.REPLAY_MISMATCH,
                "journal-effects-mismatch",
            )
        replayed_effects.append(tuple(stored_effects))
        if (
            int(row[7]) != transition.next_state.aggregate_revision.value
            or int(row[8]) != transition.next_state.admission_sequence.value
        ):
            raise _LedgerOpenError(
                ContextAdmissionStorageFailureReason.REPLAY_MISMATCH,
                "journal-result-coordinate-mismatch",
            )
        shadow_wrapper = decode_stored_context_admission_envelope(
            shadow_by_sequence[journal_sequence]
        )
        regenerated_shadow = _shadow_record(
            stream_key,
            state,
            event,
            transition,
            journal_sequence,
        )
        if (
            shadow_wrapper.protocol_version != protocol_version
            or not isinstance(
                shadow_wrapper.payload,
                ShadowContextAdmissionRecord,
            )
            or shadow_wrapper.payload != regenerated_shadow
        ):
            raise _LedgerOpenError(
                ContextAdmissionStorageFailureReason.REPLAY_MISMATCH,
                "journal-shadow-mismatch",
            )
        replayed_shadows.append(shadow_wrapper.payload)
        state = transition.next_state
    replay = context_admission_reducer_for_protocol(genesis.protocol_version).replay_stream(
        genesis,
        tuple(events),
    )
    if replay.final_state != state:
        raise _LedgerOpenError(
            ContextAdmissionStorageFailureReason.REPLAY_MISMATCH,
            "registered-replay-mismatch",
        )
    materialized_wrapper = decode_stored_context_admission_envelope(materialized_state_envelope)
    if (
        materialized_wrapper.protocol_version != state.protocol_version
        or not isinstance(materialized_wrapper.payload, _STATE_TYPES)
        or materialized_wrapper.payload != state
        or aggregate_revision != state.aggregate_revision.value
        or admission_sequence != state.admission_sequence.value
    ):
        raise _LedgerOpenError(
            ContextAdmissionStorageFailureReason.REPLAY_MISMATCH,
            "materialized-state-mismatch",
        )
    return (
        state,
        tuple(events),
        tuple(replayed_decisions),
        tuple(replayed_effects),
        tuple(replayed_shadows),
    )
