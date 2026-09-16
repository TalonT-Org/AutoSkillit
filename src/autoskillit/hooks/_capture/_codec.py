"""Strict canonical-JSON codec for shell-capture lifecycle data."""

from __future__ import annotations

import json
from typing import Any

from ._failure_policy import CaptureFailureReason
from ._module_identity import register_module_aliases

register_module_aliases(__name__)

_MAX_NESTING = 16
_MAX_JSON_NODES = 4096


class LedgerCodecError(RuntimeError):
    """Raised when framed lifecycle bytes are not strictly recoverable."""

    failure_reason = CaptureFailureReason.LEDGER_INTEGRITY
    reason = "corrupt"
    observed_version: int | None = None
    current_version: int | None = None


class _DuplicateField(ValueError):
    pass


def _object_without_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise _DuplicateField(key)
        result[key] = value
    return result


def _reject_constant(value: str) -> None:
    raise ValueError(f"non-finite constant: {value}")


def _validate_shape(value: object) -> None:
    stack: list[tuple[object, int]] = [(value, 1)]
    seen = 0
    while stack:
        current, depth = stack.pop()
        seen += 1
        if seen > _MAX_JSON_NODES or depth > _MAX_NESTING:
            raise LedgerCodecError("lifecycle frame JSON exceeds structural bound")
        if isinstance(current, dict):
            if any(not isinstance(key, str) for key in current):
                raise LedgerCodecError("lifecycle frame contains a non-string key")
            stack.extend((item, depth + 1) for item in current.values())
        elif isinstance(current, list):
            stack.extend((item, depth + 1) for item in current)
        elif current is not None and not isinstance(current, (str, int, float, bool)):
            raise LedgerCodecError("lifecycle frame contains an invalid JSON value")


def canonical_json(value: object) -> bytes:
    _validate_shape(value)
    try:
        return json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            allow_nan=False,
        ).encode("ascii")
    except (TypeError, ValueError, UnicodeError, RecursionError) as exc:
        raise LedgerCodecError("lifecycle frame is not canonically encodable") from exc


def decode_json(payload: bytes) -> dict[str, object]:
    try:
        decoded = json.loads(
            payload.decode("utf-8", errors="strict"),
            object_pairs_hook=_object_without_duplicates,
            parse_constant=_reject_constant,
        )
    except _DuplicateField as exc:
        raise LedgerCodecError("duplicate lifecycle frame field") from exc
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError, RecursionError) as exc:
        raise LedgerCodecError("invalid lifecycle frame payload") from exc
    _validate_shape(decoded)
    if canonical_json(decoded) != payload:
        raise LedgerCodecError("noncanonical lifecycle frame payload")
    if not isinstance(decoded, dict):
        raise LedgerCodecError("lifecycle frame payload is not an object")
    return decoded
