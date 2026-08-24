"""Shared serialization and validation for context-admission contracts."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import fields
from enum import StrEnum
from typing import Any, ClassVar, get_type_hints

from ._type_dispatch_identity import DispatchIdentity
from ._type_enums import (
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
from ._type_helpers import (
    CONTEXT_ADMISSION_PROTOCOL_VERSION,
    ContextAdmissionValidationError,
    UnsupportedContextAdmissionProtocolError,
    _matches_declared_type,
    _raise_invalid,
)
from ._type_results import ModelIdentity

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
            # Suppress cause: enum_value is attacker-controlled and must not
            # leak into the error message or traceback.
            raise ContextAdmissionValidationError("invalid_serialized_enum") from None
    if "__tuple__" in value:
        if set(value) != {"__tuple__"}:
            _raise_invalid("invalid_serialized_tuple")
        raw = value["__tuple__"]
        if not isinstance(raw, list):
            _raise_invalid("invalid_serialized_tuple")
        return tuple(_decode(item) for item in raw)
    if "__frozenset__" in value:
        if set(value) != {"__frozenset__"}:
            _raise_invalid("invalid_serialized_frozenset")
        raw = value["__frozenset__"]
        if not isinstance(raw, list):
            _raise_invalid("invalid_serialized_frozenset")
        return frozenset(_decode(item) for item in raw)
    type_name = value.get("__type__")
    if type_name == "ModelIdentity":
        if set(value) != {
            "__type__",
            "configured_model",
            "effective_model",
            "profile_name",
        }:
            _raise_invalid("invalid_model_identity")
        configured_model = value["configured_model"]
        effective_model = value["effective_model"]
        profile_name = value["profile_name"]
        if not all(
            isinstance(item, str) for item in (configured_model, effective_model, profile_name)
        ):
            _raise_invalid("invalid_model_identity")
        return ModelIdentity(
            configured_model=configured_model,
            effective_model=effective_model,
            profile_name=profile_name,
        )
    if not isinstance(type_name, str) or type_name not in _TYPE_REGISTRY:
        _raise_invalid("unknown_serialized_contract_type")
    contract_type = _TYPE_REGISTRY[type_name]
    kwargs = {key: _decode(item) for key, item in value.items() if key != "__type__"}
    try:
        return contract_type(**kwargs)
    except TypeError:
        # Suppress cause: kwargs come from attacker-controlled serialized
        # data and the unexpected-kwarg name must not leak.
        raise ContextAdmissionValidationError("invalid_serialized_contract") from None


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
