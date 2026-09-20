"""Canonical token usage type. Zero autoskillit imports."""

from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, TypedDict

from ._type_constants_env import AGENT_BACKEND_CLAUDE_CODE, AGENT_BACKEND_CODEX
from ._type_enums import TokenMeasureState

logger = logging.getLogger(__name__)  # noqa: TID251 — IL-0 types cannot import core.logging

# Canonical accounting field set shared by TokenEntry, fleet pair totals,
# OTLP observation aggregation, and the sidecar accounting measures frozenset.
# Order is deliberate (cache_write before cache_read) so iteration across all
# consumers stays consistent. peak_context is intentionally NOT in this tuple —
# it is a separate measure aggregated only by aggregate_token_observations.
CANONICAL_ACCOUNTING_FIELDS: tuple[str, ...] = (
    "input_tokens",
    "output_tokens",
    "cache_write_tokens",
    "cache_read_tokens",
)

__all__ = [
    "CANONICAL_ACCOUNTING_FIELDS",
    "CanonicalTokenUsage",
    "SerializedTokenMeasure",
    "TokenMeasure",
    "TurnTokenEntry",
]


class SerializedTokenMeasure(TypedDict):
    """Durable representation of one accounting measure."""

    state: str
    value: int | None


@dataclass(frozen=True, slots=True)
class TokenMeasure:
    """One token accounting observation with an explicit availability state."""

    state: TokenMeasureState
    value: int | None = None

    def __post_init__(self) -> None:
        if self.value is not None and (
            isinstance(self.value, bool) or not isinstance(self.value, int) or self.value < 0
        ):
            raise ValueError("Token measure values must be non-negative integers")
        if self.state is TokenMeasureState.MEASURED:
            if self.value is None or self.value == 0:
                raise ValueError("measured token measures require a positive value")
        elif self.state is TokenMeasureState.MEASURED_ZERO:
            if self.value != 0:
                raise ValueError("measured_zero token measures require value 0")
        elif self.value is not None:
            raise ValueError(f"{self.state.value} token measures cannot carry a value")

    @classmethod
    def observed(cls, value: int) -> TokenMeasure:
        """Build an explicitly observed measurement, including an observed zero."""
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise ValueError("Observed token values must be non-negative integers")
        return cls(
            TokenMeasureState.MEASURED_ZERO if value == 0 else TokenMeasureState.MEASURED,
            value,
        )

    @classmethod
    def unavailable(cls) -> TokenMeasure:
        return cls(TokenMeasureState.UNAVAILABLE)

    @classmethod
    def unknown(cls) -> TokenMeasure:
        return cls(TokenMeasureState.UNKNOWN)

    @classmethod
    def not_applicable(cls) -> TokenMeasure:
        return cls(TokenMeasureState.NOT_APPLICABLE)

    @classmethod
    def from_dict(cls, raw: object) -> TokenMeasure:
        """Decode a durable measure record without accepting ambiguous numerics."""
        if not isinstance(raw, dict):
            raise ValueError("Token measure must be a mapping")
        if set(raw) != {"state", "value"}:
            raise ValueError("Token measure must contain exactly state and value")
        state = raw["state"]
        value = raw["value"]
        if not isinstance(state, str):
            raise ValueError("Token measure state must be a string")
        try:
            parsed_state = TokenMeasureState(state)
        except ValueError as exc:
            raise ValueError(f"Unknown token measure state: {state!r}") from exc
        if value is not None and (isinstance(value, bool) or not isinstance(value, int)):
            raise ValueError("Token measure value must be an integer or null")
        return cls(parsed_state, value)

    @classmethod
    def measure_from_raw(cls, raw: object, *, legacy: bool = False) -> TokenMeasure:
        """Canonical decoder: dict/int/None → TokenMeasure with legacy-zero overload.

        Downgrades to unknown on malformed input while logging the offending shape.
        """
        if isinstance(raw, dict):
            try:
                return cls.from_dict(raw)
            except ValueError as exc:
                logger.debug(
                    "token_measure_decode_downgrade_to_unknown",
                    extra={
                        "raw_kind": type(raw).__name__,
                        "raw_keys": sorted(raw.keys()) if isinstance(raw, Mapping) else None,
                        "error": str(exc),
                    },
                )
                return cls.unknown()
        if isinstance(raw, int) and not isinstance(raw, bool) and raw >= 0:
            if legacy and raw == 0:
                return cls.unknown()
            return cls.observed(raw)
        return cls.unknown()

    @classmethod
    def measure_from_raw_to_serialized(
        cls, raw: object, *, legacy: bool = False
    ) -> SerializedTokenMeasure:
        """Decode through the canonical helper and serialize for durable storage."""
        return cls.measure_from_raw(raw, legacy=legacy).to_dict()

    @staticmethod
    def combine_or_unknown(left: TokenMeasure, right: TokenMeasure) -> TokenMeasure:
        """Combine two measures or downgrade to unknown on incompatible states."""
        try:
            return left.combine(right)
        except ValueError as exc:
            logger.debug(
                "token_measure_combine_downgrade_to_unknown",
                extra={
                    "left_state": left.state.value,
                    "right_state": right.state.value,
                    "error": str(exc),
                },
            )
            return TokenMeasure.unknown()

    @staticmethod
    def maximum_or_unknown(left: TokenMeasure, right: TokenMeasure) -> TokenMeasure:
        """Take the maximum of two measures or downgrade to unknown on conflict."""
        try:
            return left.maximum(right)
        except ValueError as exc:
            logger.debug(
                "token_measure_maximum_downgrade_to_unknown",
                extra={
                    "left_state": left.state.value,
                    "right_state": right.state.value,
                    "error": str(exc),
                },
            )
            return TokenMeasure.unknown()

    def to_dict(self) -> SerializedTokenMeasure:
        return {"state": self.state.value, "value": self.value}

    def combine(self, other: TokenMeasure) -> TokenMeasure:
        """Combine compatible observations without converting absence into zero."""
        if self.value is not None and other.value is not None:
            return self.observed(self.value + other.value)
        if self.state is other.state:
            return self
        raise ValueError(
            "Cannot combine token measures with incompatible availability states: "
            f"{self.state.value!r} vs {other.state.value!r}"
        )

    def maximum(self, other: TokenMeasure) -> TokenMeasure:
        """Return the observed maximum or reject incompatible availability evidence."""
        if self.value is not None and other.value is not None:
            return self.observed(max(self.value, other.value))
        if self.state is other.state:
            return self
        raise ValueError(
            "Cannot compare token measures with incompatible availability states: "
            f"{self.state.value!r} vs {other.state.value!r}"
        )


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
