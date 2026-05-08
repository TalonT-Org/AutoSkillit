"""Tests for autoskillit.core._json fast JSON wrapper."""

from __future__ import annotations

import json

import pytest

pytestmark = [pytest.mark.layer("core"), pytest.mark.small]


def test_fast_loads_dict() -> None:
    from autoskillit.core._json import fast_loads

    assert fast_loads('{"a": 1}') == {"a": 1}


def test_fast_loads_bytes_input() -> None:
    from autoskillit.core._json import fast_loads

    assert fast_loads(b'{"x": true}') == {"x": True}


def test_fast_loads_list() -> None:
    from autoskillit.core._json import fast_loads

    assert fast_loads("[1, 2, 3]") == [1, 2, 3]


def test_fast_loads_returns_standard_python_types() -> None:
    from autoskillit.core._json import fast_loads

    result = fast_loads('{"a": 1, "b": [true, null]}')
    assert isinstance(result, dict)
    assert isinstance(result["b"], list)


def test_fast_loads_raises_json_decode_error_on_invalid() -> None:
    from autoskillit.core._json import fast_loads

    with pytest.raises(json.JSONDecodeError):
        fast_loads("{not valid}")


def test_fast_dumps_returns_str() -> None:
    from autoskillit.core._json import fast_dumps

    result = fast_dumps({"a": 1})
    assert isinstance(result, str)
    assert json.loads(result) == {"a": 1}


def test_fast_dumps_sort_keys_orders_keys() -> None:
    from autoskillit.core._json import fast_dumps

    result = fast_dumps({"z": 1, "a": 2}, sort_keys=True)
    assert result.index('"a"') < result.index('"z"')


def test_fast_dumps_indent_produces_multiline() -> None:
    from autoskillit.core._json import fast_dumps

    result = fast_dumps({"a": 1}, indent=True)
    assert "\n" in result


def test_fast_dumps_default_handler_handles_set() -> None:
    from autoskillit.core._json import fast_dumps

    result = fast_dumps({"s": {"a", "b"}}, default=list)
    data = json.loads(result)
    assert set(data["s"]) == {"a", "b"}


def test_fast_dumps_roundtrip_with_fast_loads() -> None:
    from autoskillit.core._json import fast_dumps, fast_loads

    original = {"key": [1, 2, 3], "flag": True, "n": None}
    assert fast_loads(fast_dumps(original)) == original
