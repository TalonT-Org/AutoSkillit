"""Dimension-safe token, UTF-8 byte, and serialized-char limits.

Each wrapper is an ``int`` subclass: arithmetic and comparisons with plain
``int`` work transparently at runtime, while mypy treats them as distinct
types — comparing ``Utf8ByteLimit`` with ``TokenLimit`` is a type error.
The ``.value`` property returns the plain ``int`` for call sites that
explicitly extract the numeric value.
"""

from __future__ import annotations

import json
from fractions import Fraction

__all__ = [
    "ASCII_YAML_POLICY",
    "BytesToTokensPolicy",
    "CLIENT_CHARS_PER_TOKEN_POLICY",
    "CONSERVATIVE_ADMISSION_POLICY",
    "SerializedChars",
    "TokenLimit",
    "Utf8ByteLimit",
    "client_serialized_char_len",
]


class TokenLimit(int):
    """A positive token count, not interchangeable with a byte count."""

    def __new__(cls, value: int) -> TokenLimit:
        if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
            raise ValueError(f"TokenLimit must be positive; got {value!r}")
        return super().__new__(cls, value)

    @property
    def value(self) -> int:
        return int(self)


class Utf8ByteLimit(int):
    """A positive UTF-8 byte count, not interchangeable with tokens."""

    def __new__(cls, value: int) -> Utf8ByteLimit:
        if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
            raise ValueError(f"Utf8ByteLimit must be positive; got {value!r}")
        return super().__new__(cls, value)

    @property
    def value(self) -> int:
        return int(self)


class SerializedChars(int):
    """Client-measured JSON-serialized character count.

    Not interchangeable with a byte count or a token count.
    """

    def __new__(cls, value: int) -> SerializedChars:
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise ValueError(f"SerializedChars must be non-negative; got {value!r}")
        return super().__new__(cls, value)

    @property
    def value(self) -> int:
        return int(self)


def client_serialized_char_len(text: str) -> SerializedChars:
    """Return the client-measured serialized character count.

    The Claude Code client gates MCP tool results by the length of the
    JSON-serialized response text. For a string response, this equals
    len(json.dumps(text)) — the raw text plus JSON string encoding
    (quotes, backslash escapes). Verified against the incident artifact:
    134,218-byte compiled content → 189,773 serialized chars.
    """
    return SerializedChars(len(json.dumps(text)))


class BytesToTokensPolicy:
    """Explicit exact-rational conversion between byte and token domains."""

    __slots__ = ("utf8_bytes_per_token",)

    def __init__(self, utf8_bytes_per_token: Fraction) -> None:
        if not isinstance(utf8_bytes_per_token, Fraction):
            raise TypeError("utf8_bytes_per_token must be a Fraction")
        if utf8_bytes_per_token <= 0:
            raise ValueError("utf8_bytes_per_token must be positive")
        self.utf8_bytes_per_token = utf8_bytes_per_token

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
