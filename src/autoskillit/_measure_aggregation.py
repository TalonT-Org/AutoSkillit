"""Stdlib-only measure aggregation authority: availability states, the TokenMeasure
value type, and aggregation over (harness, provider) scopes.
Shared by package consumers (via autoskillit.core) and standalone hook projections
(bare-name import).
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum, unique
from typing import TypedDict

logger = logging.getLogger(__name__)  # noqa: TID251 — stdlib-only hook-callable authority

__all__ = [
    "CANONICAL_ACCOUNTING_FIELDS",
    "SerializedTokenMeasure",
    "TokenMeasure",
    "TokenMeasureState",
]


@unique
class TokenMeasureState(StrEnum):
    MEASURED = "measured"
    MEASURED_ZERO = "measured_zero"
    UNAVAILABLE = "unavailable"
    UNKNOWN = "unknown"
    NOT_APPLICABLE = "not_applicable"


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
