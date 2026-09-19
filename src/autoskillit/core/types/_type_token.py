"""Canonical token usage type. Zero autoskillit imports."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, TypedDict

from ._type_enums import TokenMeasureState

__all__ = [
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
    """Dict-shaped sidecar row; aggregate totals use ``CanonicalTokenUsage``."""

    backend: str
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

    input_tokens: int
    output_tokens: int
    cache_read_tokens: int | None
    cache_write_tokens: int | None
    provider: str
    raw: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_anthropic_dict(cls, d: dict[str, Any]) -> CanonicalTokenUsage:
        return cls(
            input_tokens=d["input_tokens"],
            output_tokens=d["output_tokens"],
            cache_read_tokens=d.get("cache_read_input_tokens"),
            cache_write_tokens=d.get("cache_creation_input_tokens"),
            provider="anthropic",
            raw=dict(d),
        )

    @classmethod
    def from_codex_dict(cls, d: dict[str, Any]) -> CanonicalTokenUsage:
        return cls(
            input_tokens=d["input_tokens"],
            output_tokens=d["output_tokens"],
            cache_read_tokens=d.get("cached_input_tokens"),
            cache_write_tokens=None,
            provider="codex",
            raw=dict(d),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "cache_read_tokens": self.cache_read_tokens
            if self.cache_read_tokens is not None
            else 0,
            "cache_write_tokens": self.cache_write_tokens
            if self.cache_write_tokens is not None
            else 0,
            "provider": self.provider,
        }

    @classmethod
    def merge(
        cls, base: CanonicalTokenUsage | None, other: CanonicalTokenUsage | None
    ) -> CanonicalTokenUsage | None:
        if base is None:
            return other
        if other is None:
            return base

        if base.provider != other.provider:
            raise ValueError(
                f"Cannot merge CanonicalTokenUsage with mismatched providers: "
                f"{base.provider!r} vs {other.provider!r}"
            )

        def _add_optional(a: int | None, b: int | None) -> int | None:
            if a is None and b is None:
                return None
            return (a or 0) + (b or 0)

        return cls(
            input_tokens=base.input_tokens + other.input_tokens,
            output_tokens=base.output_tokens + other.output_tokens,
            cache_read_tokens=_add_optional(base.cache_read_tokens, other.cache_read_tokens),
            cache_write_tokens=_add_optional(base.cache_write_tokens, other.cache_write_tokens),
            provider=base.provider,
            raw={**base.raw, **other.raw},
        )
