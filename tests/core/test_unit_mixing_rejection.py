"""Type-safety: SerializedChars and Utf8ByteLimit are nominally distinct.

Rather than depending on mypy availability in the test runner (it may not be
installed in the venv), this test verifies the runtime property that makes
unit mixing fail: the frozen dataclasses are not interchangeable at runtime
either — min() raises TypeError because they don't implement __lt__.
"""

from __future__ import annotations

import pytest

pytestmark = [pytest.mark.layer("core"), pytest.mark.small]


def test_serialized_chars_and_utf8_bytes_are_not_comparable() -> None:
    """SerializedChars and Utf8ByteLimit cannot be compared or substituted."""
    from autoskillit.core import SerializedChars, Utf8ByteLimit

    chars = SerializedChars(100)
    bytes_ = Utf8ByteLimit(100)

    # Same numeric value, but distinct types — min() must fail
    with pytest.raises(TypeError):
        min(chars, bytes_)

    # Direct comparison must also fail
    with pytest.raises(TypeError):
        chars < bytes_  # type: ignore[operator]  # noqa: B015


def test_serialized_chars_rejects_negative() -> None:
    from autoskillit.core import SerializedChars

    with pytest.raises(ValueError, match="non-negative"):
        SerializedChars(-1)


def test_serialized_chars_allows_zero() -> None:
    from autoskillit.core import SerializedChars

    assert SerializedChars(0).value == 0
