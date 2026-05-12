"""Capture type contracts for the campaign dispatch capture chain.

Zero autoskillit imports outside this sub-package. IL-0 type contract.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

__all__ = [
    "CAPTURE_VALID_VALUE_TYPES",
    "CaptureEntrySpec",
    "CaptureValueTypeError",
    "resolve_payload_field",
]

_RESULT_REF_RE = re.compile(r"^\$\{\{\s*result\.([\w-]+)\s*\}\}$")


def resolve_payload_field(entry: CaptureEntrySpec) -> str | None:
    """Extract the payload field name from a CaptureEntrySpec's ``from_`` template.

    Parses the ``${{ result.<field_name> }}`` template string and returns the
    bare field name. Returns ``None`` if the template does not match the expected
    pattern.

    This is the single source of truth for deriving the JSON field name that
    L3 sessions should emit in the sentinel block and that the extractor
    uses to look up values in the parsed payload.
    """
    m = _RESULT_REF_RE.match(entry.from_.strip())
    return m.group(1) if m else None


CAPTURE_VALID_VALUE_TYPES = frozenset({"path", "url", "string", "optional_string"})

_VALID_VALUE_TYPES = CAPTURE_VALID_VALUE_TYPES


@dataclass(frozen=True, slots=True)
class CaptureEntrySpec:
    """A single capture entry with semantic type declaration.

    Attributes:
        from_: The ``${{ result.<field_name> }}`` template string — what was previously
            the plain string value in the ``dict[str, str]`` capture spec.
        value_type: One of ``path``, ``url``, ``string``, ``optional_string``.
            Defaults to ``string`` for backward compatibility during migration.
    """

    from_: str
    value_type: str = "string"

    def __post_init__(self) -> None:
        if self.value_type not in _VALID_VALUE_TYPES:
            raise ValueError(
                f"CaptureEntrySpec.value_type must be one of {sorted(_VALID_VALUE_TYPES)}, "
                f"got {self.value_type!r}"
            )


class CaptureValueTypeError(ValueError):
    """Raised when a captured value violates its declared type contract.

    Attributes:
        key: The capture entry key that failed validation.
        value: The raw value that was provided.
        declared_type: The ``value_type`` that was declared in ``CaptureEntrySpec``.
        reason: Human-readable description of why validation failed.
    """

    def __init__(
        self,
        key: str,
        value: str,
        declared_type: str,
        reason: str,
    ) -> None:
        self.key = key
        self.value = value
        self.declared_type = declared_type
        self.reason = reason
        super().__init__(
            f"Capture entry {key!r} (declared type: {declared_type!r}) failed validation: {reason}"
        )
