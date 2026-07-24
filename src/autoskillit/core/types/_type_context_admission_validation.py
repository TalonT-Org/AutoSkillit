"""Validation primitives for the protocol-v1 context-admission values."""

from __future__ import annotations

import re
from collections.abc import Callable
from datetime import date
from types import UnionType
from typing import Any, Never, Union, get_args, get_origin

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


__all__: list[str] = []
