"""Durable, privacy-safe contracts for context-admission accounting.

This shard defines the persistence boundary without implementing storage.  Only
released protocol values can cross the durable envelope boundary; store
authority and health/results remain process-local control values.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass, fields, is_dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Protocol, TypeAlias, cast, get_args, runtime_checkable

from ._type_context_admission import (
    AcceptInputEvent,
    AdmissionBatchId,
    AdmissionDecision,
    AdmissionEffect,
    AdmissionEventId,
    AdmissionOccurrenceId,
    AdmissionReservationId,
    AdmissionSequence,
    AdmissionTransition,
    AgentInstanceId,
    AggregateRevision,
    ContextAdmissionEvent,
    ContextAdmissionState,
    ContextSessionId,
    ContextThreadId,
    DeliveryOccurrenceId,
    ForkOccurrenceId,
    GenerationReservationId,
    ProducerInstanceId,
    ReconcileGenerationEvent,
    ReleaseNonAdmissionEvent,
    ReserveRequestEvent,
    ResolveIndeterminateAcceptedEvent,
    ResolveIndeterminateNonAdmissionEvent,
    ResolveIndeterminateRollbackEvent,
    RollbackAdmissionEvent,
    ToolCallId,
    TurnId,
    WindowEpochId,
    _ContractValue,
    _decode,
    _encode,
)
from ._type_enums import (
    AdmissionState,
    ContextAdmissionAccountingStatus,
    ContextAdmissionStorageFailureReason,
    ContextAdmissionStorageHealthStatus,
    GenerationState,
    MeasurementKind,
    ProducerSurface,
    ReserveClass,
)
from ._type_helpers import (
    CONTEXT_ADMISSION_PROTOCOL_VERSION,
    ContextAdmissionValidationError,
    _raise_invalid,
    _validate_bounded_text,
    _validate_non_negative,
    _validate_protocol_version,
    _validate_reason_code,
)

CONTEXT_ADMISSION_ENCODING_VERSION = 1
_MAX_PERSISTED_TEXT = 256
_MAX_ENVELOPE_BYTES = 16 * 1024 * 1024
_MAX_JSON_NESTING = 128
_CANONICAL_ENVELOPE_KEYS = frozenset(
    {"encoding_version", "protocol_version", "type_discriminator", "payload"}
)


@dataclass(frozen=True, slots=True)
class ContextAdmissionStreamKey(_ContractValue):
    """Immutable session/thread/fork partition for one reducer stream."""

    root_session_id: ContextSessionId
    current_session_id: ContextSessionId
    root_agent_id: AgentInstanceId
    current_agent_id: AgentInstanceId
    root_thread_id: ContextThreadId
    current_thread_id: ContextThreadId
    fork_occurrence_id: ForkOccurrenceId | None


@dataclass(frozen=True, slots=True)
class ContextAdmissionStoreAuthority:
    """Process-local authority for opening one context-admission store."""

    database_path: Path
    expected_owner_id: int

    def __post_init__(self) -> None:
        if (
            not isinstance(self.database_path, Path)
            or not self.database_path.is_absolute()
            or not self.database_path.name
        ):
            raise ValueError("invalid_context_admission_store_path")
        if isinstance(self.expected_owner_id, bool) or self.expected_owner_id < 0:
            raise ValueError("invalid_context_admission_store_owner")


@dataclass(frozen=True, slots=True)
class ShadowContextAdmissionTargetRecord(_ContractValue):
    """Content-free accounting projection for one affected durable target."""

    target_id: AdmissionBatchId | GenerationReservationId
    occurrence_ids: tuple[AdmissionOccurrenceId, ...]
    turn_ids: tuple[TurnId, ...]
    tool_call_ids: tuple[ToolCallId | None, ...]
    producer_instance_ids: tuple[ProducerInstanceId, ...]
    producer_surfaces: tuple[ProducerSurface, ...]
    delivery_occurrence_ids: tuple[DeliveryOccurrenceId | None, ...]
    reservation_id: AdmissionReservationId | None
    batch_id: AdmissionBatchId | None
    generation_reservation_id: GenerationReservationId | None
    window_epoch_id: WindowEpochId | None
    reserve_class: ReserveClass | None
    lifecycle_state: AdmissionState | GenerationState
    proposed_input_count: int | None
    generation_allowance: int | None
    exact_input_charge: int | None
    exact_output_charge: int | None
    measurement_kind: MeasurementKind | None

    def __post_init__(self) -> None:
        for value in (
            self.proposed_input_count,
            self.generation_allowance,
            self.exact_input_charge,
            self.exact_output_charge,
        ):
            if value is not None:
                _validate_non_negative(value, "invalid_shadow_count")
        if tuple(sorted(self.occurrence_ids, key=lambda item: item.value)) != self.occurrence_ids:
            _raise_invalid("noncanonical_shadow_occurrences")
        occurrence_count = len(self.occurrence_ids)
        if any(
            len(values) != occurrence_count
            for values in (
                self.turn_ids,
                self.tool_call_ids,
                self.producer_instance_ids,
                self.producer_surfaces,
                self.delivery_occurrence_ids,
            )
        ):
            _raise_invalid("shadow_lineage_coordinate_mismatch")
        is_input = isinstance(self.target_id, AdmissionBatchId)
        if is_input and self.batch_id != self.target_id:
            _raise_invalid("invalid_shadow_input_target")
        if is_input and self.generation_reservation_id is not None:
            _raise_invalid("mixed_shadow_target_domain")
        if not is_input and (
            self.batch_id is None or self.generation_reservation_id != self.target_id
        ):
            _raise_invalid("invalid_shadow_generation_target")


@dataclass(frozen=True, slots=True)
class ShadowContextAdmissionRecord(_ContractValue):
    """Event-level shadow publication persisted beside one journal row."""

    stream_key: ContextAdmissionStreamKey
    event_id: AdmissionEventId
    journal_sequence: int
    aggregate_revision: AggregateRevision
    admission_sequence: AdmissionSequence
    decision: AdmissionDecision
    protocol_version: int
    encoding_version: int
    reason_code: str
    targets: tuple[ShadowContextAdmissionTargetRecord, ...]

    def __post_init__(self) -> None:
        _validate_non_negative(self.journal_sequence, "invalid_shadow_journal_sequence")
        if self.journal_sequence == 0:
            _raise_invalid("invalid_shadow_journal_sequence")
        _validate_protocol_version(self.protocol_version)
        if self.encoding_version != CONTEXT_ADMISSION_ENCODING_VERSION:
            _raise_invalid("unsupported_context_admission_encoding")
        _validate_reason_code(self.reason_code)
        target_keys = tuple(_shadow_target_key(target) for target in self.targets)
        if tuple(sorted(target_keys)) != target_keys or len(set(target_keys)) != len(target_keys):
            _raise_invalid("noncanonical_shadow_targets")


def _shadow_target_key(target: ShadowContextAdmissionTargetRecord) -> tuple[str, str]:
    return (type(target.target_id).__name__, target.target_id.value)


DurableContextAdmissionPayload: TypeAlias = (
    ContextAdmissionEvent
    | AdmissionEffect
    | ContextAdmissionState
    | AdmissionDecision
    | ShadowContextAdmissionRecord
)

_TOP_LEVEL_TYPES = tuple(
    dict.fromkeys(
        (
            *get_args(ContextAdmissionEvent),
            *get_args(AdmissionEffect),
            *get_args(ContextAdmissionState),
            AdmissionDecision,
            ShadowContextAdmissionRecord,
        )
    )
)
CONTEXT_ADMISSION_TOP_LEVEL_DISCRIMINATORS = frozenset(
    value_type.__name__ for value_type in _TOP_LEVEL_TYPES
)
_TOP_LEVEL_TYPE_REGISTRY = MappingProxyType(
    {value_type.__name__: value_type for value_type in _TOP_LEVEL_TYPES}
)


@dataclass(frozen=True, slots=True)
class StoredContextAdmissionEnvelope:
    """Versioned canonical wrapper for one released semantic value."""

    encoding_version: int
    protocol_version: int
    type_discriminator: str
    payload: DurableContextAdmissionPayload

    def __post_init__(self) -> None:
        if (
            isinstance(self.encoding_version, bool)
            or self.encoding_version != CONTEXT_ADMISSION_ENCODING_VERSION
        ):
            raise ContextAdmissionValidationError("unsupported_context_admission_encoding")
        _validate_protocol_version(self.protocol_version)
        _validate_bounded_text(
            self.type_discriminator,
            "invalid_context_admission_discriminator",
            maximum=96,
        )
        allowed_type = _TOP_LEVEL_TYPE_REGISTRY.get(self.type_discriminator)
        if allowed_type is None or type(self.payload) is not allowed_type:
            raise ContextAdmissionValidationError("invalid_context_admission_discriminator")
        validate_context_admission_persistence_value(self.payload)


def make_stored_context_admission_envelope(
    payload: DurableContextAdmissionPayload,
    *,
    protocol_version: int = CONTEXT_ADMISSION_PROTOCOL_VERSION,
    encoding_version: int = CONTEXT_ADMISSION_ENCODING_VERSION,
) -> StoredContextAdmissionEnvelope:
    """Wrap one released value with explicit storage and protocol versions."""
    return StoredContextAdmissionEnvelope(
        encoding_version=encoding_version,
        protocol_version=protocol_version,
        type_discriminator=type(payload).__name__,
        payload=payload,
    )


def encode_stored_context_admission_envelope(
    envelope: StoredContextAdmissionEnvelope,
) -> bytes:
    """Encode one envelope with the byte-stable durable JSON algorithm."""
    encoded_payload = _encode(envelope.payload)
    if not isinstance(encoded_payload, dict):
        _raise_invalid("invalid_context_admission_payload")
    payload_type = encoded_payload.pop("__type__", None)
    if payload_type != envelope.type_discriminator:
        _raise_invalid("invalid_context_admission_discriminator")
    value = {
        "encoding_version": envelope.encoding_version,
        "payload": encoded_payload,
        "protocol_version": envelope.protocol_version,
        "type_discriminator": envelope.type_discriminator,
    }
    return json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def decode_stored_context_admission_envelope(
    encoded: bytes,
) -> StoredContextAdmissionEnvelope:
    """Decode and canonicality-check one durable envelope."""
    current_encoded = _upcast_envelope_bytes(encoded)
    value = _decode_envelope_json(current_encoded)
    encoding_version, protocol_version, discriminator = _envelope_header(value)
    raw_payload = value["payload"]
    if (
        encoding_version != CONTEXT_ADMISSION_ENCODING_VERSION
        or not isinstance(raw_payload, dict)
        or discriminator not in _TOP_LEVEL_TYPE_REGISTRY
    ):
        _raise_invalid("invalid_context_admission_envelope")
    tagged_payload = {"__type__": discriminator, **raw_payload}
    payload = _decode(tagged_payload)
    envelope = StoredContextAdmissionEnvelope(
        encoding_version=encoding_version,
        protocol_version=protocol_version,
        type_discriminator=discriminator,
        payload=cast(DurableContextAdmissionPayload, payload),
    )
    if encode_stored_context_admission_envelope(envelope) != current_encoded:
        _raise_invalid("noncanonical_context_admission_envelope")
    return envelope


def decode_stored_context_admission_envelope_header(
    encoded: bytes,
) -> tuple[int, int, str]:
    """Read a bounded durable-envelope header without decoding its payload."""
    return _envelope_header(_decode_envelope_json(encoded))


def _upcast_envelope_bytes(encoded: bytes) -> bytes:
    current_encoded = encoded
    visited_versions: set[int] = set()
    while True:
        value = _decode_envelope_json(current_encoded)
        source_version, protocol_version, discriminator = _envelope_header(value)
        if source_version == CONTEXT_ADMISSION_ENCODING_VERSION:
            return current_encoded
        if source_version in visited_versions:
            _raise_invalid("ambiguous_context_admission_upcast")
        visited_versions.add(source_version)
        candidates = tuple(
            (target_version, upcaster)
            for (candidate_source, target_version), upcaster in (
                CONTEXT_ADMISSION_ENVELOPE_UPCASTERS.items()
            )
            if candidate_source == source_version
        )
        if len(candidates) != 1:
            _raise_invalid("unsupported_context_admission_encoding")
        target_version, upcaster = candidates[0]
        if target_version <= source_version or target_version > CONTEXT_ADMISSION_ENCODING_VERSION:
            _raise_invalid("ambiguous_context_admission_upcast")
        current_encoded = upcaster(current_encoded)
        if not isinstance(current_encoded, bytes):
            _raise_invalid("invalid_context_admission_upcast")
        upcast_value = _decode_envelope_json(current_encoded)
        upcast_version, upcast_protocol, upcast_discriminator = _envelope_header(upcast_value)
        if (
            upcast_version != target_version
            or upcast_protocol != protocol_version
            or upcast_discriminator != discriminator
        ):
            _raise_invalid("invalid_context_admission_upcast")


def _decode_envelope_json(encoded: bytes) -> dict[str, object]:
    if not isinstance(encoded, bytes) or not encoded or len(encoded) > _MAX_ENVELOPE_BYTES:
        _raise_invalid("invalid_context_admission_envelope")
    _validate_json_nesting(encoded)
    try:
        value = json.loads(encoded.decode("utf-8"), object_pairs_hook=_unique_object)
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError):
        raise ContextAdmissionValidationError("invalid_context_admission_envelope") from None
    if not isinstance(value, dict) or frozenset(value) != _CANONICAL_ENVELOPE_KEYS:
        _raise_invalid("invalid_context_admission_envelope")
    return value


def _validate_json_nesting(encoded: bytes) -> None:
    depth = 0
    in_string = False
    escaped = False
    for value in encoded:
        if in_string:
            if escaped:
                escaped = False
            elif value == ord("\\"):
                escaped = True
            elif value == ord('"'):
                in_string = False
            continue
        if value == ord('"'):
            in_string = True
        elif value in {ord("{"), ord("[")}:
            depth += 1
            if depth > _MAX_JSON_NESTING:
                _raise_invalid("invalid_context_admission_envelope")
        elif value in {ord("}"), ord("]")}:
            depth -= 1
            if depth < 0:
                _raise_invalid("invalid_context_admission_envelope")


def _envelope_header(value: Mapping[str, object]) -> tuple[int, int, str]:
    encoding_version = value["encoding_version"]
    protocol_version = value["protocol_version"]
    discriminator = value["type_discriminator"]
    if (
        isinstance(encoding_version, bool)
        or not isinstance(encoding_version, int)
        or isinstance(protocol_version, bool)
        or not isinstance(protocol_version, int)
        or not isinstance(discriminator, str)
    ):
        _raise_invalid("invalid_context_admission_envelope")
    return encoding_version, protocol_version, discriminator


def _unique_object(items: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in items:
        if key in result:
            raise ContextAdmissionValidationError("duplicate_context_admission_envelope_key")
        result[key] = value
    return result


def validate_context_admission_persistence_value(value: object) -> None:
    """Reject mutable, sensitive, path-like, or unbounded durable values."""
    if value is None or isinstance(value, bool | int):
        return
    if isinstance(value, str):
        _validate_persisted_text(value)
        return
    if isinstance(value, tuple | frozenset):
        for item in value:
            validate_context_admission_persistence_value(item)
        return
    if isinstance(value, Path | list | dict | set | bytearray):
        _raise_invalid("invalid_persistence_value")
    if isinstance(value, Mapping):
        for key, item in value.items():
            if not isinstance(key, str):
                _raise_invalid("invalid_persistence_mapping_key")
            _validate_persisted_text(key)
            validate_context_admission_persistence_value(item)
        return
    if is_dataclass(value):
        for field_def in fields(value):
            validate_context_admission_persistence_value(getattr(value, field_def.name))
        return
    if hasattr(value, "value") and isinstance(value.value, str):
        _validate_persisted_text(value.value)
        return
    _raise_invalid("invalid_persistence_value")


def _validate_persisted_text(value: str) -> None:
    if value == "":
        return
    _validate_bounded_text(
        value,
        "invalid_persisted_text",
        maximum=_MAX_PERSISTED_TEXT,
    )
    lowered = value.casefold()
    secret_markers = (
        "bearer ",
        "password=",
        "secret=",
        "api_key",
        "api-key",
        "capability_token",
        "ghp_",
        "sk-",
    )
    looks_like_path = (
        value.startswith(("/", "~/", "\\"))
        or (len(value) > 2 and value[1] == ":" and value[2] in {"/", "\\"})
        or "\n" in value
        or "\r" in value
    )
    looks_like_digest = len(value) in {40, 64, 128} and all(
        character in "0123456789abcdefABCDEF" for character in value
    )
    if looks_like_path or looks_like_digest or any(marker in lowered for marker in secret_markers):
        _raise_invalid("sensitive_persistence_value")


@dataclass(frozen=True, slots=True)
class ContextAdmissionStoreHealth:
    status: ContextAdmissionStorageHealthStatus
    failure_reason: ContextAdmissionStorageFailureReason | None = None
    reason_code: str | None = None

    def __post_init__(self) -> None:
        _validate_health(self.status, self.failure_reason, self.reason_code)


@dataclass(frozen=True, slots=True)
class ContextAdmissionStreamHealth:
    stream_key: ContextAdmissionStreamKey
    status: ContextAdmissionStorageHealthStatus
    failure_reason: ContextAdmissionStorageFailureReason | None = None
    reason_code: str | None = None

    def __post_init__(self) -> None:
        _validate_health(self.status, self.failure_reason, self.reason_code)


def _validate_health(
    status: ContextAdmissionStorageHealthStatus,
    failure_reason: ContextAdmissionStorageFailureReason | None,
    reason_code: str | None,
) -> None:
    failed = status is ContextAdmissionStorageHealthStatus.FAIL_CLOSED
    if failed != (failure_reason is not None):
        raise ValueError("invalid_context_admission_storage_health")
    if failed != (reason_code is not None):
        raise ValueError("invalid_context_admission_storage_health")
    if reason_code is not None:
        _validate_reason_code(reason_code)


@dataclass(frozen=True, slots=True)
class ContextAdmissionAccountingResult:
    status: ContextAdmissionAccountingStatus
    stream_key: ContextAdmissionStreamKey | None
    transition: AdmissionTransition | None = None
    journal_sequence: int | None = None
    failure_reason: ContextAdmissionStorageFailureReason | None = None
    reason_code: str | None = None

    def __post_init__(self) -> None:
        if self.journal_sequence is not None:
            _validate_non_negative(self.journal_sequence, "invalid_journal_sequence")
            if self.journal_sequence == 0:
                raise ValueError("invalid_journal_sequence")
        if self.reason_code is not None:
            _validate_reason_code(self.reason_code)
        if self.status is ContextAdmissionAccountingStatus.RECORDED:
            if self.transition is None or self.journal_sequence is None:
                raise ValueError("recorded_result_requires_publication")
        elif self.status is ContextAdmissionAccountingStatus.EXACT_REPLAY:
            if self.transition is None or self.transition.effects or self.journal_sequence is None:
                raise ValueError("exact_replay_result_is_not_idempotent")
        elif self.status in {
            ContextAdmissionAccountingStatus.CONTENDED,
            ContextAdmissionAccountingStatus.STORAGE_FAIL_CLOSED,
        }:
            if self.transition is not None or self.journal_sequence is not None:
                raise ValueError("nonadmitting_storage_result_has_transition")
        if self.status is ContextAdmissionAccountingStatus.STORAGE_FAIL_CLOSED:
            if self.failure_reason is None:
                raise ValueError("storage_failure_requires_reason")
        elif self.failure_reason is not None:
            raise ValueError("nonstorage_result_has_storage_reason")


@dataclass(frozen=True, slots=True)
class ContextAdmissionRecoveryResult:
    status: ContextAdmissionStorageHealthStatus
    store_health: ContextAdmissionStoreHealth
    stream_healths: tuple[ContextAdmissionStreamHealth, ...]
    recovered_streams: tuple[ContextAdmissionStreamKey, ...]
    unresolved_streams: tuple[ContextAdmissionStreamKey, ...]

    def __post_init__(self) -> None:
        if self.status is not self.store_health.status:
            raise ValueError("recovery_status_mismatch")


@dataclass(frozen=True, slots=True)
class ContextAdmissionInspectionResult:
    stream_key: ContextAdmissionStreamKey
    health: ContextAdmissionStreamHealth
    state: ContextAdmissionState | None
    events: tuple[ContextAdmissionEvent, ...]
    decisions: tuple[AdmissionDecision, ...]
    effects: tuple[tuple[AdmissionEffect, ...], ...]
    shadows: tuple[ShadowContextAdmissionRecord, ...]
    latest_journal_sequence: int

    def __post_init__(self) -> None:
        if self.stream_key != self.health.stream_key:
            raise ValueError("inspection_stream_health_identity_mismatch")
        _validate_non_negative(
            self.latest_journal_sequence,
            "invalid_latest_journal_sequence",
        )
        lengths = {
            len(self.events),
            len(self.decisions),
            len(self.effects),
            len(self.shadows),
        }
        if len(lengths) != 1:
            raise ValueError("inspection_publication_length_mismatch")


@runtime_checkable
class ContextAdmissionLedger(Protocol):
    """Durable reducer publication and recovery service."""

    def store_health(self) -> ContextAdmissionStoreHealth: ...

    def stream_health(
        self,
        stream_key: ContextAdmissionStreamKey,
    ) -> ContextAdmissionStreamHealth: ...

    def apply(
        self,
        stream_key: ContextAdmissionStreamKey,
        event: ContextAdmissionEvent,
    ) -> ContextAdmissionAccountingResult: ...

    def reserve(
        self,
        stream_key: ContextAdmissionStreamKey,
        event: ReserveRequestEvent,
    ) -> ContextAdmissionAccountingResult: ...

    def commit(
        self,
        stream_key: ContextAdmissionStreamKey,
        event: (AcceptInputEvent | ResolveIndeterminateAcceptedEvent | ReconcileGenerationEvent),
    ) -> ContextAdmissionAccountingResult: ...

    def release(
        self,
        stream_key: ContextAdmissionStreamKey,
        event: (
            ReleaseNonAdmissionEvent
            | RollbackAdmissionEvent
            | ResolveIndeterminateNonAdmissionEvent
            | ResolveIndeterminateRollbackEvent
        ),
    ) -> ContextAdmissionAccountingResult: ...

    def recover(
        self,
        stream_key: ContextAdmissionStreamKey,
    ) -> ContextAdmissionRecoveryResult: ...

    def recover_all(self) -> ContextAdmissionRecoveryResult: ...

    def replay(
        self,
        stream_key: ContextAdmissionStreamKey,
    ) -> ContextAdmissionInspectionResult: ...

    def inspect_stream(
        self,
        stream_key: ContextAdmissionStreamKey,
    ) -> ContextAdmissionInspectionResult: ...


CONTEXT_ADMISSION_ENVELOPE_UPCASTERS: Mapping[
    tuple[int, int],
    Callable[[bytes], bytes],
] = MappingProxyType({})

__all__ = [
    "CONTEXT_ADMISSION_ENCODING_VERSION",
    "CONTEXT_ADMISSION_TOP_LEVEL_DISCRIMINATORS",
    "CONTEXT_ADMISSION_ENVELOPE_UPCASTERS",
    "ContextAdmissionStreamKey",
    "ContextAdmissionStoreAuthority",
    "ShadowContextAdmissionTargetRecord",
    "ShadowContextAdmissionRecord",
    "DurableContextAdmissionPayload",
    "StoredContextAdmissionEnvelope",
    "ContextAdmissionStoreHealth",
    "ContextAdmissionStreamHealth",
    "ContextAdmissionAccountingResult",
    "ContextAdmissionRecoveryResult",
    "ContextAdmissionInspectionResult",
    "ContextAdmissionLedger",
    "make_stored_context_admission_envelope",
    "encode_stored_context_admission_envelope",
    "decode_stored_context_admission_envelope",
    "decode_stored_context_admission_envelope_header",
    "validate_context_admission_persistence_value",
]
