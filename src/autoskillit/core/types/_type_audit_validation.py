"""Shared validation helpers for audit-admission value contracts."""

from __future__ import annotations

from pathlib import Path

from ..closure_hashing import HASH_RE


def _require_nonempty(name: str, value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string")
    return value


def _require_digest(name: str, value: object) -> str:
    if not isinstance(value, str) or HASH_RE.fullmatch(value) is None:
        raise ValueError(f"{name} must be an algorithm-qualified sha256 digest")
    return value


def _require_optional_digest(name: str, value: object) -> str | None:
    if value is None:
        return None
    return _require_digest(name, value)


def _require_absolute_path(name: str, value: object) -> Path:
    if not isinstance(value, Path) or not value.is_absolute() or ".." in value.parts:
        raise ValueError(f"{name} must be an absolute non-traversing Path")
    return value
