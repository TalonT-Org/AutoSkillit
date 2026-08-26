"""Protocol-v1 envelope codec and stream-key serialization helpers.

Pure byte-to-dataclass and dataclass-to-byte functions with no SQLite or
transaction concerns. Owns the protocol-v1 envelope codec plus stream-key
serialization and the type-tuple constants used to validate stored payloads.

Wavefront 1 of #4667.
"""

from __future__ import annotations

import json
from typing import Final, cast, get_args

from autoskillit.core import (
    AdmissionDecision,
    AdmissionEffect,
    ContextAdmissionEvent,
    ContextAdmissionState,
    ContextAdmissionStreamKey,
    ContextAdmissionValidationError,
    DurableContextAdmissionPayload,
    ShadowContextAdmissionRecord,
    decode_stored_context_admission_envelope,
    encode_stored_context_admission_envelope,
    make_stored_context_admission_envelope,
)

from ._storage import ContextAdmissionStorageFailureReason, _LedgerOpenError

_MAX_STREAM_KEY_BYTES: Final = 16 * 1024
_MAX_STREAM_KEY_JSON_NESTING: Final = 16
_EVENT_TYPES: Final = get_args(ContextAdmissionEvent)
_EFFECT_TYPES: Final = get_args(AdmissionEffect)
_STATE_TYPES: Final = get_args(ContextAdmissionState)


def _stream_key_bytes(stream_key: ContextAdmissionStreamKey) -> bytes:
    return json.dumps(
        stream_key.to_dict(),
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def _zero_state(protocol_version: int):
    from autoskillit.core import (
        AdmissionSequence,
        AggregateRevision,
        UninitializedContextAdmissionState,
        context_admission_reducer_for_protocol,
    )

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


def _decode_effect(value: bytes) -> AdmissionEffect:
    payload = decode_stored_context_admission_envelope(value).payload
    if not isinstance(payload, _EFFECT_TYPES):
        raise ContextAdmissionValidationError("stored_effect_type_mismatch")
    return cast(AdmissionEffect, payload)


def _decode_shadow(value: bytes) -> ShadowContextAdmissionRecord:
    payload = decode_stored_context_admission_envelope(value).payload
    if not isinstance(payload, ShadowContextAdmissionRecord):
        raise ContextAdmissionValidationError("stored_shadow_type_mismatch")
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
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError):
        raise _LedgerOpenError(
            ContextAdmissionStorageFailureReason.IDENTITY_MISMATCH,
            "invalid-stream-key",
        ) from None
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
