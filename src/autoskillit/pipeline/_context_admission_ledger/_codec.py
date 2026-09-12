"""Protocol-v1 envelope codec and stream-key serialization helpers.

Owns the protocol-v1 envelope codec, stream-key serialization, type-tuple
constants used to validate stored payloads, and the recursive dataclass walker
used to extract lineage identity from decoded events.

Wavefront 1 of #4667.
"""

from __future__ import annotations

import json
from dataclasses import fields, is_dataclass
from typing import Final, cast, get_args

from autoskillit.core import (
    AdmissionDecision,
    AdmissionEffect,
    AdmissionSequence,
    AggregateRevision,
    ContextAdmissionEvent,
    ContextAdmissionState,
    ContextAdmissionStorageFailureReason,
    ContextAdmissionStreamKey,
    ContextAdmissionValidationError,
    ContextLineage,
    DurableContextAdmissionPayload,
    UninitializedContextAdmissionState,
    context_admission_reducer_for_protocol,
    decode_stored_context_admission_envelope,
    encode_stored_context_admission_envelope,
    make_stored_context_admission_envelope,
)

from ._storage import _LedgerOpenError

_MAX_STREAM_KEY_BYTES: Final = 16 * 1024
_MAX_STREAM_KEY_JSON_NESTING: Final = 16
_EVENT_TYPES: Final = get_args(ContextAdmissionEvent)
_EFFECT_TYPES: Final = get_args(AdmissionEffect)
_STATE_TYPES: Final = get_args(ContextAdmissionState)

__all__ = [
    "_MAX_STREAM_KEY_BYTES",
    "_MAX_STREAM_KEY_JSON_NESTING",
    "_EVENT_TYPES",
    "_EFFECT_TYPES",
    "_STATE_TYPES",
    "_stream_key_bytes",
    "_zero_state",
    "_encode_value",
    "_decode_event",
    "_decode_decision",
    "_decode_state",
    "_decode_stream_key",
    "_iter_lineages",
    "_validate_stream_key_json_bounds",
]


def _stream_key_bytes(stream_key: ContextAdmissionStreamKey) -> bytes:
    return json.dumps(
        stream_key.to_dict(),
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def _zero_state(protocol_version: int) -> UninitializedContextAdmissionState:
    context_admission_reducer_for_protocol(protocol_version)
    return UninitializedContextAdmissionState(
        protocol_version=protocol_version,
        aggregate_revision=AggregateRevision(0),
        admission_sequence=AdmissionSequence(0),
        processed_events=(),
        idempotency_records=(),
        expired_idempotency_tombstones=(),
        closed_epochs=(),
    )


def _encode_value(
    value: DurableContextAdmissionPayload,
    *,
    protocol_version: int,
) -> bytes:
    return encode_stored_context_admission_envelope(
        make_stored_context_admission_envelope(
            value,
            protocol_version=protocol_version,
        )
    )


def _decode_event(value: bytes) -> ContextAdmissionEvent:
    payload = decode_stored_context_admission_envelope(value).payload
    if not isinstance(payload, _EVENT_TYPES):
        raise ContextAdmissionValidationError("stored_event_type_mismatch")
    return cast(ContextAdmissionEvent, payload)


def _decode_decision(value: bytes) -> AdmissionDecision:
    payload = decode_stored_context_admission_envelope(value).payload
    if not isinstance(payload, AdmissionDecision):
        raise ContextAdmissionValidationError("stored_decision_type_mismatch")
    return payload


def _decode_state(value: bytes) -> ContextAdmissionState:
    payload = decode_stored_context_admission_envelope(value).payload
    if not isinstance(payload, _STATE_TYPES):
        raise ContextAdmissionValidationError("stored_state_type_mismatch")
    return cast(ContextAdmissionState, payload)


def _decode_stream_key(value: bytes) -> ContextAdmissionStreamKey:
    _validate_stream_key_json_bounds(value)
    try:
        raw = json.loads(value.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError) as exc:
        raise _LedgerOpenError(
            ContextAdmissionStorageFailureReason.IDENTITY_MISMATCH,
            "invalid-stream-key",
        ) from exc
    if not isinstance(raw, dict):
        raise _LedgerOpenError(
            ContextAdmissionStorageFailureReason.IDENTITY_MISMATCH,
            "invalid-stream-key",
        )
    try:
        stream_key = ContextAdmissionStreamKey.from_dict(raw)
    except ContextAdmissionValidationError as exc:
        raise _LedgerOpenError(
            ContextAdmissionStorageFailureReason.IDENTITY_MISMATCH,
            "invalid-stream-key",
        ) from exc
    if _stream_key_bytes(stream_key) != value:
        raise _LedgerOpenError(
            ContextAdmissionStorageFailureReason.IDENTITY_MISMATCH,
            "noncanonical-stream-key",
        )
    return stream_key


def _iter_lineages(value: object) -> tuple[ContextLineage, ...]:
    lineages: list[ContextLineage] = []

    def visit(item: object) -> None:
        if isinstance(item, ContextLineage):
            lineages.append(item)
            return
        if isinstance(item, tuple | frozenset):
            for child in item:
                visit(child)
            return
        if is_dataclass(item):
            for field_def in fields(item):
                visit(getattr(item, field_def.name))

    visit(value)
    return tuple(lineages)


def _validate_stream_key_json_bounds(value: bytes) -> None:
    if not value or len(value) > _MAX_STREAM_KEY_BYTES:
        raise _LedgerOpenError(
            ContextAdmissionStorageFailureReason.IDENTITY_MISMATCH,
            "invalid-stream-key",
        )
    depth = 0
    in_string = False
    escaped = False
    for character in value:
        if in_string:
            if escaped:
                escaped = False
            elif character == ord("\\"):
                escaped = True
            elif character == ord('"'):
                in_string = False
            continue
        if character == ord('"'):
            in_string = True
        elif character in {ord("{"), ord("[")}:
            depth += 1
            if depth > _MAX_STREAM_KEY_JSON_NESTING:
                raise _LedgerOpenError(
                    ContextAdmissionStorageFailureReason.IDENTITY_MISMATCH,
                    "invalid-stream-key",
                )
        elif character in {ord("}"), ord("]")}:
            depth -= 1
            if depth < 0:
                raise _LedgerOpenError(
                    ContextAdmissionStorageFailureReason.IDENTITY_MISMATCH,
                    "invalid-stream-key",
                )
