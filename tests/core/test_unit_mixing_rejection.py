"""Type-safety: SerializedChars and Utf8ByteLimit are nominally distinct.

Both are ``int`` subclasses — runtime arithmetic works transparently.
Cross-unit misuse is caught by mypy (static type checking). This test
verifies the runtime properties: distinct types, correct `.value`,
and construction-time validation.
"""

from __future__ import annotations

import pytest

pytestmark = [pytest.mark.layer("core"), pytest.mark.small]


def test_serialized_chars_and_utf8_bytes_are_distinct_int_subclasses() -> None:
    """SerializedChars and Utf8ByteLimit are distinct types (same int base)."""
    from autoskillit.core import SerializedChars, Utf8ByteLimit

    chars = SerializedChars(100)
    bytes_ = Utf8ByteLimit(100)

    # Same numeric value, distinct types — both are int subclasses
    assert type(chars) is not type(bytes_)
    assert isinstance(chars, int) and isinstance(bytes_, int)
    assert chars == bytes_  # same numeric value via int comparison
    assert chars.value == bytes_.value == 100


def test_serialized_chars_rejects_negative() -> None:
    from autoskillit.core import SerializedChars

    with pytest.raises(ValueError, match="non-negative"):
        SerializedChars(-1)


def test_serialized_chars_allows_zero() -> None:
    from autoskillit.core import SerializedChars

    assert SerializedChars(0).value == 0
