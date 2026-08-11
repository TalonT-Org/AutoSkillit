"""Unit tests for client_serialized_char_len."""

from __future__ import annotations

import json

import pytest

from autoskillit.core import SerializedChars, client_serialized_char_len

pytestmark = [pytest.mark.layer("core"), pytest.mark.small]


def test_plain_ascii_equals_len_plus_quotes() -> None:
    text = "hello world"
    result = client_serialized_char_len(text)
    assert isinstance(result, SerializedChars)
    assert result.value == len(text) + 2  # quotes


def test_backslash_dense_string_exceeds_raw_length() -> None:
    text = 'a\\b"c\nd'
    result = client_serialized_char_len(text)
    assert result.value == len(json.dumps(text))
    assert result.value > len(text)


def test_embedded_json_inflation() -> None:
    """A string containing embedded JSON has serialized length strictly
    exceeding raw length due to escaping of inner quotes/backslashes."""
    inner = json.dumps({"key": "value", "nested": [1, 2, 3]})
    result = client_serialized_char_len(inner)
    assert result.value > len(inner)
    assert result.value == len(json.dumps(inner))


def test_empty_string() -> None:
    result = client_serialized_char_len("")
    assert result.value == 2  # just the quotes: ""


def test_serialized_chars_rejects_negative() -> None:
    with pytest.raises(ValueError, match="non-negative"):
        SerializedChars(-1)


def test_serialized_chars_allows_zero() -> None:
    assert SerializedChars(0).value == 0
