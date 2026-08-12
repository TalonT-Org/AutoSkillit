"""Dimension-safe output-budget value objects."""

from fractions import Fraction
from typing import cast

import pytest

from autoskillit.core import ASCII_YAML_POLICY, BytesToTokensPolicy, TokenLimit, Utf8ByteLimit

pytestmark = [pytest.mark.layer("core"), pytest.mark.small]


def test_token_and_utf8_byte_limits_are_distinct_positive_types() -> None:
    token_limit = TokenLimit(46_500)
    byte_limit = Utf8ByteLimit(46_500)

    # Both are int subclasses — runtime arithmetic works transparently.
    # Cross-unit misuse is caught by mypy (static type checking), not
    # at runtime. Verify they are distinct types but numerically equal.
    assert type(token_limit) is not type(byte_limit)
    assert token_limit == byte_limit  # same numeric value
    assert isinstance(token_limit, int) and isinstance(byte_limit, int)


@pytest.mark.parametrize("limit_type", [TokenLimit, Utf8ByteLimit])
@pytest.mark.parametrize("value", [0, -1, True, 1.5])
def test_dimension_limits_reject_non_positive_or_non_integer_values(
    limit_type: type[TokenLimit] | type[Utf8ByteLimit],
    value: object,
) -> None:
    with pytest.raises(ValueError, match="must be positive"):
        limit_type(cast(int, value))


def test_bytes_to_tokens_conversion_uses_exact_explicit_policy() -> None:
    policy = ASCII_YAML_POLICY

    assert policy.utf8_bytes_per_token == Fraction(27, 10)
    assert policy.to_tokens(Utf8ByteLimit(100_000)) == TokenLimit(37_038)
    assert policy.to_bytes(TokenLimit(37_038)) == Utf8ByteLimit(100_002)


def test_bytes_to_tokens_policy_rejects_non_fraction_ratio() -> None:
    with pytest.raises(TypeError, match="must be a Fraction"):
        BytesToTokensPolicy(cast(Fraction, 2.7))


@pytest.mark.parametrize("ratio", [Fraction(0), Fraction(-1)])
def test_bytes_to_tokens_policy_rejects_non_positive_ratio(ratio: Fraction) -> None:
    with pytest.raises(ValueError, match="must be positive"):
        BytesToTokensPolicy(ratio)
