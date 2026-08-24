"""Shared validation helpers for immutable audit-admission contracts."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ..closure_hashing import HASH_RE


def _require_nonempty(name: str, value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string")
    return value


def _require_digest(name: str, value: object) -> str:
    if not isinstance(value, str) or HASH_RE.fullmatch(value) is None:
        raise ValueError(f"{name} must be an algorithm-qualified sha256 digest")
    return value


def _require_positive_int(name: str, value: object, *, allow_zero: bool = False) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{name} must be an integer")
    minimum = 0 if allow_zero else 1
    if value < minimum:
        raise ValueError(f"{name} must be >= {minimum}")
    return value


def _require_optional_digest(name: str, value: object) -> str | None:
    if value is None:
        return None
    return _require_digest(name, value)


def _require_absolute_path(name: str, value: object) -> Path:
    if not isinstance(value, Path) or not value.is_absolute() or ".." in value.parts:
        raise ValueError(f"{name} must be an absolute non-traversing Path")
    return value


def _require_tracker_target(
    owner: str,
    tracker_expected: object,
    tracker_target_order_id: object,
) -> None:
    if type(tracker_expected) is not bool:
        raise ValueError(f"{owner}.tracker_expected must be a boolean")
    if tracker_target_order_id is not None:
        _require_nonempty(f"{owner}.tracker_target_order_id", tracker_target_order_id)
    if tracker_expected and tracker_target_order_id is None:
        raise ValueError("expected tracker authority requires a target order id")


def _typed_tuple(name: str, value: object, item_type: type[Any]) -> tuple[Any, ...]:
    if not isinstance(value, tuple) or not all(isinstance(item, item_type) for item in value):
        raise ValueError(f"{name} must be a tuple of {item_type.__name__} values")
    return value
