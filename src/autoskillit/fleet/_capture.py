"""Capture spec extraction and validation."""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path

from autoskillit.core import (
    CaptureEntrySpec,
    CaptureValueTypeError,
    get_logger,
    resolve_payload_field,
)

logger = get_logger(__name__)


class CaptureCompletenessError(RuntimeError):
    """Raised when a capture spec extracts zero fields from the payload."""


def _validate_capture_value(key: str, value: str, declared_type: str) -> None:
    """Validate a captured value against its declared type.

    Raises CaptureValueTypeError if validation fails.
    """
    if declared_type == "path":
        if not value:
            raise CaptureValueTypeError(
                key=key,
                value=value,
                declared_type=declared_type,
                reason="path value must be non-empty",
            )
        if not Path(value).exists():
            raise CaptureValueTypeError(
                key=key,
                value=value,
                declared_type=declared_type,
                reason=f"path does not exist: {value}",
            )
    elif declared_type == "string":
        if not value:
            raise CaptureValueTypeError(
                key=key,
                value=value,
                declared_type=declared_type,
                reason="string value must be non-empty",
            )
    elif declared_type == "url":
        if not value:
            raise CaptureValueTypeError(
                key=key,
                value=value,
                declared_type=declared_type,
                reason="url value must be non-empty",
            )
        if not (
            value.startswith("http://")
            or value.startswith("https://")
            or value.startswith("file://")
        ):
            raise CaptureValueTypeError(
                key=key,
                value=value,
                declared_type=declared_type,
                reason=f"url value must start with http://, https://, or file://: {value!r}",
            )


def _extract_captures(
    capture_spec: dict[str, CaptureEntrySpec],
    payload: dict[str, object],
) -> dict[str, str]:
    """Extract captured values from an L3 result payload.

    For each entry in `capture_spec`, reads `payload[field_name]` from the
    ``from_`` template and validates it against the declared `value_type`.
    Missing payload keys are logged as warnings. If the capture spec has
    entries but all fields are absent from the payload, raises
    CaptureCompletenessError. If a value fails type validation,
    raises CaptureValueTypeError.
    """
    result: dict[str, str] = {}
    expected_fields: list[str] = []
    for key, entry in capture_spec.items():
        field_name = resolve_payload_field(entry)
        if field_name is None:
            continue
        expected_fields.append(field_name)
        if field_name in payload:
            value: object = payload[field_name]
            if not isinstance(value, str) and entry.value_type == "path":
                raise CaptureValueTypeError(
                    key=key,
                    value=repr(value),
                    declared_type=entry.value_type,
                    reason=f"expected a string path, got {type(value).__name__}",
                )
            str_value = value if isinstance(value, str) else json.dumps(value, default=str)
            _validate_capture_value(key, str_value, entry.value_type)
            result[key] = str_value
        else:
            logger.warning(
                "capture_field_missing_from_payload",
                capture_name=key,
                expected_field=field_name,
                available_fields=sorted(str(k) for k in payload.keys()),
            )
    if expected_fields and not result:
        raise CaptureCompletenessError(
            f"Capture spec expected fields {expected_fields} but none were "
            f"present in payload. Available: {sorted(str(k) for k in payload.keys())}. "
            f"This indicates a sentinel/capture misalignment."
        )
    return result


def _normalize_capture_spec(
    capture: Mapping[str, str | CaptureEntrySpec] | None,
) -> dict[str, CaptureEntrySpec] | None:
    """Convert YAML-format ``dict[str, str]`` capture spec to ``dict[str, CaptureEntrySpec]``.

    The recipe YAML uses shorthand capture entries: ``{key: "${{ result.field }}"}``.
    This converts them to the typed ``CaptureEntrySpec`` format used internally.
    Already-typed ``CaptureEntrySpec`` values are passed through unchanged.
    """
    if capture is None:
        return None
    return {
        key: val if isinstance(val, CaptureEntrySpec) else CaptureEntrySpec(from_=val)
        for key, val in capture.items()
    }
