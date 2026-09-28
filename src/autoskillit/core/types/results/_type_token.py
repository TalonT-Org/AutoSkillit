"""Canonical token usage types.

The measure primitives and the aggregation API are re-exported from the
stdlib-only root authority autoskillit._measure_aggregation.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, TypedDict

from autoskillit._measure_aggregation import (
    CANONICAL_ACCOUNTING_FIELDS,
    MAXIMUM_FIELDS,
    TOKEN_CLASS_FIELDS,
    FieldAggregate,
    MeasureAggregate,
    MeasureRatio,
    MeasureRecord,
    MeasureScope,
    SerializedTokenMeasure,
    SourcePair,
    TokenMeasure,
    TokenNormalization,
    UnnormalizedPoolError,
    aggregate_measures,
    group_by_pair,
    measure_ratio,
    render_measure,
    render_ratio,
)

from ..constants._type_constants_env import AGENT_BACKEND_CLAUDE_CODE, AGENT_BACKEND_CODEX

__all__ = [
    "CANONICAL_ACCOUNTING_FIELDS",
    "FieldAggregate",
    "MAXIMUM_FIELDS",
    "MeasureAggregate",
    "MeasureRatio",
    "MeasureRecord",
    "MeasureScope",
    "SourcePair",
    "TOKEN_CLASS_FIELDS",
    "TokenNormalization",
    "UnnormalizedPoolError",
    "aggregate_measures",
    "group_by_pair",
    "measure_ratio",
    "render_measure",
    "render_ratio",
    "CanonicalTokenUsage",
    "SerializedTokenMeasure",
    "TokenMeasure",
    "TurnTokenEntry",
]


class TurnTokenEntry(TypedDict):
    """Raw turn evidence prior to sidecar classification."""

    backend: str
    provider_used: str
    message_id: str | None
    request_id: str | None
    timestamp: str | None
    model: str | None
    input_tokens: int | None
    output_tokens: int | None
    cache_read_tokens: int | None
    cache_creation_tokens: int | None
    context_window_tokens: int | None
    context_fraction: float | None


@dataclass(frozen=True, slots=True)
class CanonicalTokenUsage:
    """Provider-normalized token usage snapshot for a single session turn."""

    backend: str
    provider_used: str
    input_tokens: TokenMeasure
    output_tokens: TokenMeasure
    cache_read_tokens: TokenMeasure
    cache_write_tokens: TokenMeasure
    raw: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.backend:
            raise ValueError("Canonical token usage requires a non-empty backend")
        if not self.provider_used:
            raise ValueError("Canonical token usage requires a non-empty provider_used")
        for field_name in (
            "input_tokens",
            "output_tokens",
            "cache_read_tokens",
            "cache_write_tokens",
        ):
            value = getattr(self, field_name)
            if not isinstance(value, TokenMeasure):
                raise TypeError(
                    f"CanonicalTokenUsage.{field_name} must be a TokenMeasure, "
                    f"got {type(value).__name__}"
                )

    @staticmethod
    def _observed_or_unknown(raw: dict[str, Any], field_name: str) -> TokenMeasure:
        value = raw.get(field_name)
        if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
            return TokenMeasure.observed(value)
        return TokenMeasure.unknown()

    @classmethod
    def from_anthropic_dict(cls, d: dict[str, Any]) -> CanonicalTokenUsage:
        return cls(
            backend=AGENT_BACKEND_CLAUDE_CODE,
            provider_used="anthropic",
            input_tokens=cls._observed_or_unknown(d, "input_tokens"),
            output_tokens=cls._observed_or_unknown(d, "output_tokens"),
            cache_read_tokens=cls._observed_or_unknown(d, "cache_read_input_tokens"),
            cache_write_tokens=cls._observed_or_unknown(d, "cache_creation_input_tokens"),
            raw=dict(d),
        )

    @classmethod
    def from_codex_dict(cls, d: dict[str, Any]) -> CanonicalTokenUsage:
        return cls(
            backend=AGENT_BACKEND_CODEX,
            provider_used="codex",
            input_tokens=cls._observed_or_unknown(d, "input_tokens"),
            output_tokens=cls._observed_or_unknown(d, "output_tokens"),
            cache_read_tokens=cls._observed_or_unknown(d, "cached_input_tokens"),
            cache_write_tokens=(
                cls._observed_or_unknown(d, "cache_write_input_tokens")
                if "cache_write_input_tokens" in d
                else TokenMeasure.unavailable()
            ),
            raw=dict(d),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "backend": self.backend,
            "provider_used": self.provider_used,
            "input_tokens": self.input_tokens.to_dict(),
            "output_tokens": self.output_tokens.to_dict(),
            "cache_read_tokens": self.cache_read_tokens.to_dict(),
            "cache_write_tokens": self.cache_write_tokens.to_dict(),
        }

    @classmethod
    def merge(
        cls, base: CanonicalTokenUsage | None, other: CanonicalTokenUsage | None
    ) -> CanonicalTokenUsage | None:
        if base is None:
            return other
        if other is None:
            return base

        if (base.backend, base.provider_used) != (other.backend, other.provider_used):
            raise ValueError(
                "Cannot merge CanonicalTokenUsage with mismatched source pairs: "
                f"{(base.backend, base.provider_used)!r} vs "
                f"{(other.backend, other.provider_used)!r}"
            )

        return cls(
            backend=base.backend,
            provider_used=base.provider_used,
            input_tokens=base.input_tokens.combine(other.input_tokens),
            output_tokens=base.output_tokens.combine(other.output_tokens),
            cache_read_tokens=base.cache_read_tokens.combine(other.cache_read_tokens),
            cache_write_tokens=base.cache_write_tokens.combine(other.cache_write_tokens),
            raw={**base.raw, **other.raw},
        )
