"""Capture type contracts for the campaign dispatch capture chain.

Zero autoskillit imports outside this sub-package. IL-0 type contract.
"""

from __future__ import annotations

from dataclasses import dataclass

__all__ = ["CaptureEntrySpec", "CaptureValueTypeError"]


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
