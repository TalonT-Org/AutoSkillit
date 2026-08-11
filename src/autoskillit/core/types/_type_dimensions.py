"""Dimension-safe token and UTF-8 byte limits."""

from __future__ import annotations

from dataclasses import dataclass
from fractions import Fraction

__all__ = [
    "ASCII_YAML_POLICY",
    "BytesToTokensPolicy",
    "CLIENT_CHARS_PER_TOKEN_POLICY",
    "CONSERVATIVE_ADMISSION_POLICY",
    "TokenLimit",
    "Utf8ByteLimit",
]


@dataclass(frozen=True, slots=True)
class TokenLimit:
    """A positive token count, not interchangeable with a byte count."""

    value: int

    def __post_init__(self) -> None:
        if type(self.value) is not int or self.value <= 0:
            raise ValueError(f"TokenLimit must be positive; got {self.value!r}")


@dataclass(frozen=True, slots=True)
class Utf8ByteLimit:
    """A positive UTF-8 byte count, not interchangeable with tokens."""

    value: int

    def __post_init__(self) -> None:
        if type(self.value) is not int or self.value <= 0:
            raise ValueError(f"Utf8ByteLimit must be positive; got {self.value!r}")


@dataclass(frozen=True, slots=True)
class BytesToTokensPolicy:
    """Explicit exact-rational conversion between byte and token domains."""

    utf8_bytes_per_token: Fraction

    def __post_init__(self) -> None:
        if not isinstance(self.utf8_bytes_per_token, Fraction):
            raise TypeError("utf8_bytes_per_token must be a Fraction")
        if self.utf8_bytes_per_token <= 0:
            raise ValueError("utf8_bytes_per_token must be positive")

    def to_tokens(self, byte_limit: Utf8ByteLimit) -> TokenLimit:
        numerator = self.utf8_bytes_per_token.numerator
        denominator = self.utf8_bytes_per_token.denominator
        return TokenLimit((byte_limit.value * denominator + numerator - 1) // numerator)

    def to_bytes(self, token_limit: TokenLimit) -> Utf8ByteLimit:
        ratio = self.utf8_bytes_per_token
        return Utf8ByteLimit(token_limit.value * ratio.numerator // ratio.denominator)


ASCII_YAML_POLICY = BytesToTokensPolicy(Fraction(27, 10))

# The Claude Code CLI (v2.1.220, verified via binary inspection) estimates
# 4 chars per token and budgets truncation at token_limit × 4 chars.
# This policy mirrors the client's own heuristic for response-budget
# projection ceilings — used only by the delivery-bound spill path in
# _response_budget.py to convert a token limit to a char/byte ceiling.
CLIENT_CHARS_PER_TOKEN_POLICY = BytesToTokensPolicy(Fraction(4, 1))

# Conservative 1:1 bytes-per-token admission policy for delivery-mode
# decisions. Codex tokenization can merge bytes into one token but cannot
# require more tokens than the number of input bytes — so using the exact
# byte count is intentionally conservative for delivery-mode admission.
CONSERVATIVE_ADMISSION_POLICY = BytesToTokensPolicy(Fraction(1, 1))
