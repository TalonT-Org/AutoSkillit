"""Shared serialization and validation for context-admission contracts."""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping
from dataclasses import fields
from datetime import date
from enum import StrEnum
from types import UnionType
from typing import Any, ClassVar, Never, Union, cast, get_args, get_origin, get_type_hints

from ..foundation._type_enums import (
    AdmissionDecisionKind,
    AdmissionState,
    ChargeDomain,
    CoverageEvidenceKind,
    CoverageState,
    GenerationState,
    MeasurementKind,
    ProducerSurface,
    ReserveClass,
    WitnessKind,
)
from ..launch._type_dispatch_identity import DispatchIdentity
from ..results._type_results import ModelIdentity

CONTEXT_ADMISSION_PROTOCOL_VERSION = 1
_MAX_UINT64 = (1 << 64) - 1
_CONTENT_FREE_TEXT = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:@+-]*\Z")
_CONTENT_FREE_LOCATOR = re.compile(r"[A-Za-z0-9][A-Za-z0-9_./:@+-]*\Z")
_REASON_CODE = re.compile(r"[a-z][a-z0-9-]{0,63}\Z")
_GIT_REVISION = re.compile(r"[0-9a-fA-F]{40}\Z")
_ISO_DATE = re.compile(r"\d{4}-\d{2}-\d{2}\Z")
_FRESHNESS_POLICIES = frozenset(
    {
        "verify_on_version_or_configuration_change",
        "verify_on_revision_change",
        "infer_only",
    }
)
_SENSITIVE_TEXT_MARKERS = (
    "authorization",
    "bearer",
    "content:",
    "password",
    "secret",
    "token=",
)


class ContextAdmissionValidationError(ValueError):
    """Raised when a protocol value violates a content-free invariant."""


class UnsupportedContextAdmissionProtocolError(ContextAdmissionValidationError):
    """Raised when a value uses unsupported protocol semantics."""


def _raise_invalid(reason_code: str) -> Never:
    raise ContextAdmissionValidationError(reason_code)


def _validate_protocol_version(protocol_version: int) -> None:
    if protocol_version != CONTEXT_ADMISSION_PROTOCOL_VERSION:
        raise UnsupportedContextAdmissionProtocolError("unsupported_protocol_version")


def _validate_non_negative(value: int, reason_code: str) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0 or value > _MAX_UINT64:
        _raise_invalid(reason_code)


def _reconciled_snapshot_counts(
    active_count: int,
    remaining_count: int,
    hard_limit: int,
    deducted_charge: int,
    terminal_charge: int,
) -> tuple[int, int]:
    charge_delta = deducted_charge - terminal_charge
    if charge_delta > 0:
        capacity_slack = max(hard_limit - active_count - remaining_count, 0)
        active_credit = min(max(charge_delta - capacity_slack, 0), active_count)
        restored_count = min(charge_delta, capacity_slack + active_credit)
        return active_count - active_credit, remaining_count + restored_count
    additional_charge = min(-charge_delta, remaining_count)
    return active_count + additional_charge, remaining_count - additional_charge


def _validate_bounded_text(
    value: str,
    reason_code: str,
    *,
    maximum: int = 128,
    locator: bool = False,
) -> None:
    if not isinstance(value, str) or not value or len(value) > maximum:
        _raise_invalid(reason_code)
    lowered = value.casefold()
    pattern = _CONTENT_FREE_LOCATOR if locator else _CONTENT_FREE_TEXT
    if (
        any(marker in lowered for marker in _SENSITIVE_TEXT_MARKERS)
        or lowered.startswith("sha256:")
        or lowered.startswith("blake2:")
        or "\n" in value
        or "\r" in value
        or value.startswith("/")
        or "\\" in value
        or value.startswith("~")
        or not pattern.fullmatch(value)
        or (locator and ".." in value.split("/"))
    ):
        _raise_invalid(reason_code)


def _validate_reason_code(
    value: str,
    validation_error: str = "invalid_reason_code",
) -> None:
    _validate_bounded_text(value, validation_error, maximum=64)
    if not _REASON_CODE.fullmatch(value):
        _raise_invalid(validation_error)


def _validate_iso_date(value: str) -> None:
    if not isinstance(value, str) or not _ISO_DATE.fullmatch(value):
        _raise_invalid("invalid_checked_at")
    try:
        date.fromisoformat(value)
    except ValueError:
        raise ContextAdmissionValidationError("invalid_checked_at") from None


def _validate_tuple(value: object, reason_code: str) -> None:
    if not isinstance(value, tuple):
        _raise_invalid(reason_code)


def _validate_canonical_tuple(
    value: tuple[Any, ...],
    reason_code: str,
    *,
    key: Callable[[Any], Any],
) -> None:
    _validate_tuple(value, reason_code)
    if value != tuple(sorted(value, key=key)):
        _raise_invalid(reason_code)


def _validate_git_revision(value: str) -> None:
    if not isinstance(value, str) or not _GIT_REVISION.fullmatch(value):
        _raise_invalid("invalid_tested_revision")


def _validate_freshness_policy(value: str) -> None:
    if not isinstance(value, str) or value not in _FRESHNESS_POLICIES:
        _raise_invalid("invalid_freshness_policy")
    if value != "verify_on_version_or_configuration_change":
        _raise_invalid("unsupported_coverage_freshness_policy")


def _validate_expired_idempotency_tombstone(tombstone: Any) -> None:
    descriptor = tombstone.original_descriptor
    input_reservations = descriptor.input_reservations
    batch = descriptor.batch
    if (
        tombstone.namespace != descriptor.idempotency_namespace
        or tombstone.reservation_key.idempotency_namespace != tombstone.namespace
        or len(input_reservations) != 1
        or tombstone.reservation_key != input_reservations[0].key
        or tombstone.reservation_key.batch_id != batch.batch_id
        or tombstone.original_terminal_decision.window_epoch_id
        != tombstone.reservation_key.window_epoch_id
        or tombstone.original_terminal_decision.snapshot_sequence != descriptor.snapshot_sequence
    ):
        _raise_invalid("idempotency_tombstone_identity_mismatch")
    witness = tombstone.expiry_witness
    if (
        witness.kind is not WitnessKind.IDEMPOTENCY_EXPIRY
        or witness.window_epoch_id != tombstone.reservation_key.window_epoch_id
        or witness.window_epoch_number != tombstone.reservation_key.window_epoch_number
        or witness.snapshot_sequence != descriptor.snapshot_sequence
        or witness.request_id != batch.request_id
        or witness.batch_id != batch.batch_id
        or witness.representation_revision != batch.manifest.representation_revision
        or witness.representation_binding_id != batch.manifest.representation_binding_id
        or witness.occurrence_ids != batch.occurrence_ids
    ):
        _raise_invalid("idempotency_tombstone_witness_mismatch")


def _validate_context_admission_state_metadata(
    aggregate_revision: Any,
    admission_sequence: Any,
    processed_events: tuple[Any, ...],
    idempotency_records: tuple[Any, ...],
    expired_tombstones: tuple[Any, ...],
    closed_epochs: tuple[Any, ...],
) -> None:
    if len({record.event_id for record in processed_events}) != len(processed_events):
        _raise_invalid("duplicate_processed_event")
    processed_revisions = tuple(record.aggregate_revision.value for record in processed_events)
    processed_sequences = tuple(record.admission_sequence.value for record in processed_events)
    if (
        any(revision > aggregate_revision.value for revision in processed_revisions)
        or any(sequence > admission_sequence.value for sequence in processed_sequences)
        or any(
            later < earlier for earlier, later in zip(processed_sequences, processed_sequences[1:])
        )
    ):
        _raise_invalid("invalid_processed_event_coordinates")
    idempotency_keys = tuple(
        (record.namespace, record.reservation_key) for record in idempotency_records
    )
    if len(set(idempotency_keys)) != len(idempotency_keys):
        _raise_invalid("duplicate_idempotency_owner")
    processed_by_event_id = {record.event_id: record for record in processed_events}
    if any(
        record.publication_revision.value > aggregate_revision.value
        or (processed := processed_by_event_id.get(record.owning_event_id)) is None
        or record.publication_revision != processed.aggregate_revision
        for record in idempotency_records
    ):
        _raise_invalid("invalid_idempotency_publication_coordinates")
    tombstone_keys = tuple(
        (record.namespace, record.reservation_key) for record in expired_tombstones
    )
    if len(set(tombstone_keys)) != len(tombstone_keys):
        _raise_invalid("duplicate_idempotency_tombstone")
    epoch_keys = tuple(
        (audit.snapshot.window_epoch_id, audit.snapshot.window_epoch_number)
        for audit in closed_epochs
    )
    if len(set(epoch_keys)) != len(epoch_keys):
        _raise_invalid("duplicate_closed_epoch")


def _matches_declared_type(value: object, declared_type: object) -> bool:
    if declared_type is Any:
        return True
    origin = get_origin(declared_type)
    if origin in {Union, UnionType}:
        return any(_matches_declared_type(value, member) for member in get_args(declared_type))
    if origin is tuple:
        if type(value) is not tuple:
            return False
        members = get_args(declared_type)
        if len(members) == 2 and members[1] is Ellipsis:
            return all(_matches_declared_type(item, members[0]) for item in value)
        return len(value) == len(members) and all(
            _matches_declared_type(item, member)
            for item, member in zip(value, members, strict=True)
        )
    if origin is frozenset:
        if type(value) is not frozenset:
            return False
        (member_type,) = get_args(declared_type)
        return all(_matches_declared_type(item, member_type) for item in value)
    if declared_type is None or declared_type is type(None):
        return value is None
    if isinstance(declared_type, type):
        return type(value) is declared_type
    return False


__all__ = [
    "CONTEXT_ADMISSION_PROTOCOL_VERSION",
    "ContextAdmissionValidationError",
    "UnsupportedContextAdmissionProtocolError",
]

_TYPE_REGISTRY: dict[str, type[_ContractValue]] = {}
_ENUM_REGISTRY: dict[str, type[StrEnum]] = {
    enum_type.__name__: enum_type
    for enum_type in (
        AdmissionDecisionKind,
        AdmissionState,
        ChargeDomain,
        CoverageEvidenceKind,
        CoverageState,
        GenerationState,
        MeasurementKind,
        ProducerSurface,
        ReserveClass,
        WitnessKind,
    )
}


def _encode(value: object) -> object:
    if isinstance(value, DispatchIdentity):
        try:
            validated = DispatchIdentity(
                dispatch_id=value.dispatch_id,
                completion_marker=value.completion_marker,
                sentinel_open=value.sentinel_open,
                sentinel_close=value.sentinel_close,
                sentinel_contract=value.sentinel_contract,
            )
        except ValueError:
            _raise_invalid("invalid_dispatch_identity")
        return {"dispatch_id": validated.dispatch_id}
    if isinstance(value, ModelIdentity):
        return {
            "__type__": "ModelIdentity",
            "configured_model": value.configured_model,
            "effective_model": value.effective_model,
            "profile_name": value.profile_name,
        }
    if isinstance(value, StrEnum):
        return {"__enum__": type(value).__name__, "value": value.value}
    if isinstance(value, tuple):
        return {"__tuple__": [_encode(item) for item in value]}
    if isinstance(value, frozenset):
        encoded = [_encode(item) for item in value]
        encoded.sort(key=repr)
        return {"__frozenset__": encoded}
    if isinstance(value, _ContractValue):
        result: dict[str, object] = {"__type__": type(value).__name__}
        for field_name in value.__dataclass_fields__:
            result[field_name] = _encode(getattr(value, field_name))
        return result
    if value is None or isinstance(value, bool | int | str):
        return value
    _raise_invalid("unsupported_serialization_value")


def _decode_enum(value: Mapping[object, object]) -> StrEnum:
    if set(value) != {"__enum__", "value"}:
        _raise_invalid("unknown_serialized_enum")
    enum_name = value.get("__enum__")
    enum_value = value.get("value")
    if (
        not isinstance(enum_name, str)
        or enum_name not in _ENUM_REGISTRY
        or not isinstance(enum_value, str)
    ):
        _raise_invalid("unknown_serialized_enum")
    try:
        return _ENUM_REGISTRY[enum_name](enum_value)
    except (TypeError, ValueError):
        # Suppress cause: enum_value is attacker-controlled and must not leak
        # into the error message or traceback.
        raise ContextAdmissionValidationError("invalid_serialized_enum") from None


def _decode_tagged_items(
    value: Mapping[object, object],
    tag: str,
    reason: str,
) -> tuple[object, ...] | frozenset[object]:
    if set(value) != {tag}:
        _raise_invalid(reason)
    raw = value[tag]
    if not isinstance(raw, list):
        _raise_invalid(reason)
    decoded = tuple(_decode(item) for item in raw)
    return frozenset(decoded) if tag == "__frozenset__" else decoded


def _decode_model_identity(value: Mapping[object, object]) -> ModelIdentity:
    if set(value) != {"__type__", "configured_model", "effective_model", "profile_name"}:
        _raise_invalid("invalid_model_identity")
    configured_model = value["configured_model"]
    effective_model = value["effective_model"]
    profile_name = value["profile_name"]
    if not all(
        isinstance(item, str) for item in (configured_model, effective_model, profile_name)
    ):
        _raise_invalid("invalid_model_identity")
    return ModelIdentity(
        configured_model=cast(str, configured_model),
        effective_model=cast(str, effective_model),
        profile_name=cast(str, profile_name),
    )


def _decode_contract(value: Mapping[object, object], type_name: object) -> object:
    if not isinstance(type_name, str) or type_name not in _TYPE_REGISTRY:
        _raise_invalid("unknown_serialized_contract_type")
    contract_type = _TYPE_REGISTRY[type_name]
    kwargs = cast(
        dict[str, object],
        {key: _decode(item) for key, item in value.items() if key != "__type__"},
    )
    try:
        return contract_type(**kwargs)
    except TypeError:
        # Suppress cause: kwargs come from attacker-controlled serialized data
        # and the unexpected-kwarg name must not leak.
        raise ContextAdmissionValidationError("invalid_serialized_contract") from None


def _decode(value: object) -> object:
    if isinstance(value, list):
        return tuple(_decode(item) for item in value)
    if not isinstance(value, Mapping):
        return value
    if set(value) == {"dispatch_id"}:
        dispatch_id = value["dispatch_id"]
        if not isinstance(dispatch_id, str):
            _raise_invalid("invalid_dispatch_identity")
        return DispatchIdentity.from_dispatch_id(dispatch_id)
    if "__enum__" in value:
        return _decode_enum(value)
    if "__tuple__" in value:
        return _decode_tagged_items(value, "__tuple__", "invalid_serialized_tuple")
    if "__frozenset__" in value:
        return _decode_tagged_items(value, "__frozenset__", "invalid_serialized_frozenset")
    type_name = value.get("__type__")
    if type_name == "ModelIdentity":
        return _decode_model_identity(value)
    return _decode_contract(value, type_name)


class _ContractMeta(type):
    def __call__(cls, *args: Any, **kwargs: Any) -> Any:
        try:
            instance = super().__call__(*args, **kwargs)
        except ContextAdmissionValidationError:
            raise
        except (AttributeError, TypeError, ValueError):
            # Suppress cause: the field value that triggered the error is
            # caller-controlled and must not leak into error text or
            # tracebacks.
            raise ContextAdmissionValidationError("invalid_contract_field_type") from None
        _validate_declared_field_types(instance)
        _validate_deep_immutability(instance)
        return instance


class _ContractValue(metaclass=_ContractMeta):
    """Canonical content-free serialization shared by all protocol values."""

    _registry: ClassVar[dict[str, type[_ContractValue]]] = _TYPE_REGISTRY
    __dataclass_fields__: ClassVar[dict[str, Any]]

    def __init_subclass__(cls) -> None:
        super().__init_subclass__()
        _TYPE_REGISTRY[cls.__name__] = cls

    def to_dict(self) -> dict[str, object]:
        encoded = _encode(self)
        if not isinstance(encoded, dict):
            _raise_invalid("invalid_contract_serialization")
        encoded.pop("__type__", None)
        return encoded

    @classmethod
    def from_dict(cls, data: Mapping[str, object]) -> Any:
        if not isinstance(data, Mapping):
            _raise_invalid("invalid_serialized_contract")
        tagged = {"__type__": cls.__name__, **dict(data)}
        decoded = _decode(tagged)
        if not isinstance(decoded, cls):
            _raise_invalid("serialized_contract_type_mismatch")
        return decoded


def _validate_deep_immutability(value: object) -> None:
    if isinstance(value, list | dict | set):
        _raise_invalid("mutable_contract_collection")
    if isinstance(value, tuple | frozenset):
        for item in value:
            _validate_deep_immutability(item)
    elif isinstance(value, _ContractValue):
        for field_name in value.__dataclass_fields__:
            _validate_deep_immutability(getattr(value, field_name))


def _validate_declared_field_types(value: _ContractValue) -> None:
    declared_types = get_type_hints(type(value))
    for declared_field in fields(value):
        declared_type = declared_types.get(declared_field.name)
        if declared_type is None or not _matches_declared_type(
            getattr(value, declared_field.name),
            declared_type,
        ):
            _raise_invalid("invalid_contract_field_type")
