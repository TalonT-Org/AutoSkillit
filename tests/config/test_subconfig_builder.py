"""Tests for _coerce_value, _build_subconfig, and related helpers."""

import dataclasses
from typing import Any

import pytest

from autoskillit.config.settings import (
    _FIELD_OVERRIDES,
    _YAML_KEY_ALIASES,
    _build_subconfig,
    _coerce_value,
    _field_defaults,
)

pytestmark = [pytest.mark.layer("config"), pytest.mark.small]


# ---------------------------------------------------------------------------
# Helper test dataclass
# ---------------------------------------------------------------------------


@dataclasses.dataclass
class SimpleConfig:
    x: int = 1
    y: str = "a"


# ---------------------------------------------------------------------------
# T1: test_coerce_value_int
# ---------------------------------------------------------------------------


def test_coerce_value_int() -> None:
    assert _coerce_value(42, int, "x.y") == 42
    assert _coerce_value("42", int, "x.y") == 42
    with pytest.raises(Exception) as exc_info:
        _coerce_value("abc", int, "x.y")
    assert "x.y" in str(exc_info.value)


# ---------------------------------------------------------------------------
# T2: test_coerce_value_float
# ---------------------------------------------------------------------------


def test_coerce_value_float() -> None:
    assert _coerce_value(1.5, float, "x.y") == 1.5
    assert _coerce_value("2.5", float, "x.y") == 2.5
    with pytest.raises(Exception) as exc_info:
        _coerce_value("abc", float, "x.y")
    assert "x.y" in str(exc_info.value)


# ---------------------------------------------------------------------------
# T3: test_coerce_value_bool
# ---------------------------------------------------------------------------


def test_coerce_value_bool() -> None:
    assert _coerce_value(True, bool, "x.y") is True
    assert _coerce_value(0, bool, "x.y") is False


# ---------------------------------------------------------------------------
# T4: test_coerce_value_str
# ---------------------------------------------------------------------------


def test_coerce_value_str() -> None:
    assert _coerce_value("hello", str, "x.y") == "hello"
    assert _coerce_value(42, str, "x.y") == "42"


# ---------------------------------------------------------------------------
# T5: test_coerce_value_list
# ---------------------------------------------------------------------------


def test_coerce_value_list() -> None:
    assert _coerce_value(["a", "b"], list[str], "x.y") == ["a", "b"]
    assert _coerce_value(("a",), list[str], "x.y") == ["a"]


# ---------------------------------------------------------------------------
# T6: test_coerce_value_set
# ---------------------------------------------------------------------------


def test_coerce_value_set() -> None:
    assert _coerce_value(["a", "b"], set[str], "x.y") == {"a", "b"}


# ---------------------------------------------------------------------------
# T7: test_coerce_value_dict_passthrough
# ---------------------------------------------------------------------------


def test_coerce_value_dict_passthrough() -> None:
    d = {"k": "v"}
    assert _coerce_value(d, dict[str, str], "x.y") is d


# ---------------------------------------------------------------------------
# T8: test_coerce_value_str_or_none
# ---------------------------------------------------------------------------


def test_coerce_value_str_or_none() -> None:
    assert _coerce_value("hello", str | None, "x.y") == "hello"
    assert _coerce_value("", str | None, "x.y") is None
    assert _coerce_value(None, str | None, "x.y") is None


# ---------------------------------------------------------------------------
# T9: test_coerce_value_list_or_none
# ---------------------------------------------------------------------------


def test_coerce_value_list_or_none() -> None:
    assert _coerce_value(["a"], list[str] | None, "x.y") == ["a"]
    assert _coerce_value([], list[str] | None, "x.y") is None
    assert _coerce_value(None, list[str] | None, "x.y") is None


# ---------------------------------------------------------------------------
# T10: test_coerce_value_bool_or_none_preserves_false
# ---------------------------------------------------------------------------


def test_coerce_value_bool_or_none_preserves_false() -> None:
    assert _coerce_value(False, bool | None, "x.y") is False
    assert _coerce_value(True, bool | None, "x.y") is True
    assert _coerce_value(None, bool | None, "x.y") is None


# ---------------------------------------------------------------------------
# T11: test_build_subconfig_simple
# ---------------------------------------------------------------------------


def test_build_subconfig_simple() -> None:
    result = _build_subconfig(SimpleConfig, {"x": 5, "y": "b"}, "simple")
    assert result.x == 5
    assert result.y == "b"


# ---------------------------------------------------------------------------
# T12: test_build_subconfig_missing_key_uses_default
# ---------------------------------------------------------------------------


def test_build_subconfig_missing_key_uses_default() -> None:
    result = _build_subconfig(SimpleConfig, {}, "simple")
    assert result.x == 1
    assert result.y == "a"


# ---------------------------------------------------------------------------
# T13: test_build_subconfig_yaml_key_alias
# ---------------------------------------------------------------------------


def test_build_subconfig_yaml_key_alias(monkeypatch: pytest.MonkeyPatch) -> None:
    # Temporarily add an alias for "simple.x" -> "aliased_x"
    monkeypatch.setitem(_YAML_KEY_ALIASES, ("simple", "x"), "aliased_x")
    result = _build_subconfig(SimpleConfig, {"aliased_x": 99}, "simple")
    assert result.x == 99


# ---------------------------------------------------------------------------
# T14: test_build_subconfig_field_override
# ---------------------------------------------------------------------------


def test_build_subconfig_field_override(monkeypatch: pytest.MonkeyPatch) -> None:
    override_called: list[Any] = []

    def my_override(sec: dict[str, Any], defs: dict[str, Any]) -> Any:
        override_called.append((sec, defs))
        return 999

    monkeypatch.setitem(_FIELD_OVERRIDES, ("simple", "x"), my_override)
    result = _build_subconfig(SimpleConfig, {"x": 5}, "simple")
    assert result.x == 999
    assert override_called


# ---------------------------------------------------------------------------
# T15: test_build_subconfig_discovers_all_production_fields
# ---------------------------------------------------------------------------


def test_build_subconfig_discovers_all_production_fields() -> None:
    """Regression gate: every production sub-config dataclass must be handled."""
    from autoskillit.config.settings import (
        _SECTION_BUILDERS,
        AutomationConfig,
    )

    for f in dataclasses.fields(AutomationConfig):
        if f.name in ("features", "experimental_enabled"):
            continue
        if f.name in _SECTION_BUILDERS:
            continue
        if f.default_factory is dataclasses.MISSING or not dataclasses.is_dataclass(
            f.default_factory
        ):
            continue

        cls = f.default_factory
        defaults = _field_defaults(cls)
        section_name = f.name
        result = _build_subconfig(cls, dict(defaults), section_name)

        for sf in dataclasses.fields(cls):
            assert hasattr(result, sf.name), (
                f"{cls.__name__}.{sf.name} not populated by _build_subconfig"
            )
