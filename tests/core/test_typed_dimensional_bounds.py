"""Dimension-safe output-budget value objects."""

from fractions import Fraction

import pytest

from autoskillit.core import BytesToTokensPolicy, TokenLimit, Utf8ByteLimit

pytestmark = [pytest.mark.layer("core"), pytest.mark.small]


def test_token_and_utf8_byte_limits_are_distinct_positive_types() -> None:
    token_limit = TokenLimit(46_500)
    byte_limit = Utf8ByteLimit(46_500)

    assert not isinstance(token_limit, Utf8ByteLimit)
    assert not isinstance(byte_limit, TokenLimit)
    with pytest.raises(TypeError):
        min(token_limit, byte_limit)


def test_bytes_to_tokens_conversion_uses_exact_explicit_policy() -> None:
    policy = BytesToTokensPolicy.ASCII_YAML_POLICY

    assert policy.utf8_bytes_per_token == Fraction(27, 10)
    assert policy.to_tokens(Utf8ByteLimit(100_000)) == TokenLimit(37_038)
