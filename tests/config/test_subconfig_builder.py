"""Tests for _coerce_value, _build_subconfig, and related helpers."""

import dataclasses
from typing import Any

import pytest

from autoskillit.config.settings import (
    _FIELD_OVERRIDES,
    _YAML_KEY_ALIASES,
    _build_subconfig,
    _coerce_value,
)

pytestmark = [pytest.mark.layer("config"), pytest.mark.small]


@dataclasses.dataclass
class SimpleConfig:
    x: int = 1
    y: str = "a"


def test_coerce_value_int() -> None:
    assert _coerce_value(42, int, "x.y") == 42
    assert _coerce_value("42", int, "x.y") == 42
    with pytest.raises(Exception) as exc_info:
        _coerce_value("abc", int, "x.y")
    assert "x.y" in str(exc_info.value)


def test_coerce_value_float() -> None:
    assert _coerce_value(1.5, float, "x.y") == 1.5
    assert _coerce_value("2.5", float, "x.y") == 2.5
    with pytest.raises(Exception) as exc_info:
        _coerce_value("abc", float, "x.y")
    assert "x.y" in str(exc_info.value)


def test_coerce_value_bool() -> None:
    assert _coerce_value(True, bool, "x.y") is True
    assert _coerce_value(0, bool, "x.y") is False


def test_coerce_value_str() -> None:
    assert _coerce_value("hello", str, "x.y") == "hello"
    assert _coerce_value(42, str, "x.y") == "42"


def test_coerce_value_list() -> None:
    assert _coerce_value(["a", "b"], list[str], "x.y") == ["a", "b"]
    assert _coerce_value(("a",), list[str], "x.y") == ["a"]


def test_coerce_value_set() -> None:
    assert _coerce_value(["a", "b"], set[str], "x.y") == {"a", "b"}


def test_coerce_value_dict_passthrough() -> None:
    d = {"k": "v"}
    assert _coerce_value(d, dict[str, str], "x.y") is d


def test_coerce_value_str_or_none() -> None:
    assert _coerce_value("hello", str | None, "x.y") == "hello"
    assert _coerce_value("", str | None, "x.y") is None
    assert _coerce_value(None, str | None, "x.y") is None


def test_coerce_value_list_or_none() -> None:
    assert _coerce_value(["a"], list[str] | None, "x.y") == ["a"]
    assert _coerce_value([], list[str] | None, "x.y") is None
    assert _coerce_value(None, list[str] | None, "x.y") is None


def test_coerce_value_bool_or_none_preserves_false() -> None:
    assert _coerce_value(False, bool | None, "x.y") is False
    assert _coerce_value(True, bool | None, "x.y") is True
    assert _coerce_value(None, bool | None, "x.y") is None


def test_build_subconfig_simple() -> None:
    result = _build_subconfig(SimpleConfig, {"x": 5, "y": "b"}, "simple")
    assert result.x == 5
    assert result.y == "b"


def test_build_subconfig_missing_key_uses_default() -> None:
    result = _build_subconfig(SimpleConfig, {}, "simple")
    assert result.x == 1
    assert result.y == "a"


def test_build_subconfig_yaml_key_alias(monkeypatch: pytest.MonkeyPatch) -> None:
    # Temporarily add an alias for "simple.x" -> "aliased_x"
    monkeypatch.setitem(_YAML_KEY_ALIASES, ("simple", "x"), "aliased_x")
    result = _build_subconfig(SimpleConfig, {"aliased_x": 99}, "simple")
    assert result.x == 99


def test_build_subconfig_field_override(monkeypatch: pytest.MonkeyPatch) -> None:
    override_called: list[Any] = []

    def my_override(sec: dict[str, Any], defs: dict[str, Any]) -> Any:
        override_called.append((sec, defs))
        return 999

    monkeypatch.setitem(_FIELD_OVERRIDES, ("simple", "x"), my_override)
    result = _build_subconfig(SimpleConfig, {"x": 5}, "simple")
    assert result.x == 999
    assert override_called
