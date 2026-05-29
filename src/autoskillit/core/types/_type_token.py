"""Canonical token usage type. Zero autoskillit imports."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

__all__ = ["CanonicalTokenUsage"]


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
            "cache_read_tokens": self.cache_read_tokens,
            "cache_write_tokens": self.cache_write_tokens,
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
