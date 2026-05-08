"""Tests for autoskillit.core._json fast JSON wrapper."""

from __future__ import annotations

import importlib
import json
import sys

import pytest

from autoskillit.core._json import fast_dumps, fast_loads

pytestmark = [pytest.mark.layer("core"), pytest.mark.small]


def test_fast_loads_dict() -> None:
    assert fast_loads('{"a": 1}') == {"a": 1}


def test_fast_loads_bytes_input() -> None:
    assert fast_loads(b'{"x": true}') == {"x": True}


def test_fast_loads_list() -> None:
    assert fast_loads("[1, 2, 3]") == [1, 2, 3]


def test_fast_loads_returns_standard_python_types() -> None:
    result = fast_loads('{"a": 1, "b": [true, null]}')
    assert isinstance(result, dict)
    assert isinstance(result["b"], list)


def test_fast_loads_raises_json_decode_error_on_invalid() -> None:
    with pytest.raises(json.JSONDecodeError):
        fast_loads("{not valid}")


def test_fast_dumps_returns_str() -> None:
    result = fast_dumps({"a": 1})
    assert isinstance(result, str)
    assert json.loads(result) == {"a": 1}


def test_fast_dumps_sort_keys_orders_keys() -> None:
    result = fast_dumps({"z": 1, "a": 2}, sort_keys=True)
    assert result.index('"a"') < result.index('"z"')


def test_fast_dumps_indent_produces_multiline() -> None:
    result = fast_dumps({"a": 1}, indent=True)
    assert (
        '  "a"' in result
    )  # both orjson (OPT_INDENT_2) and stdlib (indent=2) produce 2-space indent


def test_fast_dumps_default_handler_handles_set() -> None:
    result = fast_dumps({"s": {"a", "b"}}, default=list)
    data = json.loads(result)
    assert set(data["s"]) == {"a", "b"}


def test_fast_dumps_roundtrip_with_fast_loads() -> None:
    original = {"key": [1, 2, 3], "flag": True, "n": None}
    assert fast_loads(fast_dumps(original)) == original


@pytest.fixture()
def stdlib_json_mod(monkeypatch: pytest.MonkeyPatch):
    """Provides _json reimported with orjson blocked to exercise the stdlib fallback."""
    monkeypatch.setitem(sys.modules, "orjson", None)  # type: ignore[arg-type]
    monkeypatch.delitem(sys.modules, "autoskillit.core._json", raising=False)
    return importlib.import_module("autoskillit.core._json")


def test_stdlib_fallback_fast_loads(stdlib_json_mod) -> None:
    assert not hasattr(stdlib_json_mod, "_orjson"), (
        "orjson must not be accessible in the stdlib fallback module"
    )
    assert stdlib_json_mod._USE_ORJSON is False, (
        "_USE_ORJSON sentinel must be False when orjson is blocked"
    )
    assert stdlib_json_mod.fast_loads('{"a": 1}') == {"a": 1}
    assert stdlib_json_mod.fast_loads(b'{"x": true}') == {"x": True}
    with pytest.raises(json.JSONDecodeError):
        stdlib_json_mod.fast_loads("{not valid}")


def test_stdlib_fallback_fast_dumps(stdlib_json_mod) -> None:
    result = stdlib_json_mod.fast_dumps({"a": 1})
    assert isinstance(result, str)
    assert json.loads(result) == {"a": 1}
    assert '  "a"' in stdlib_json_mod.fast_dumps({"a": 1}, indent=True)
