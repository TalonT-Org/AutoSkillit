"""Tests for recipe/_registry_utils shared utilities."""

from __future__ import annotations

from pathlib import Path

import pytest

from autoskillit.recipe._registry_utils import parse_int_field

pytestmark = [pytest.mark.layer("recipe"), pytest.mark.small]


def test_parse_int_field_returns_int_from_string() -> None:
    assert parse_int_field({"priority": "5"}, "priority", 999, Path("test.yaml"), "Widget") == 5


def test_parse_int_field_returns_int_from_int() -> None:
    assert parse_int_field({"priority": 3}, "priority", 999, Path("test.yaml"), "Widget") == 3


def test_parse_int_field_returns_default_when_missing() -> None:
    assert parse_int_field({}, "priority", 999, Path("test.yaml"), "Widget") == 999


def test_parse_int_field_raises_on_non_numeric() -> None:
    with pytest.raises(TypeError, match="Widget.*'abc_thing'.*priority.*must be an integer"):
        parse_int_field(
            {"name": "abc_thing", "priority": "not_a_number"},
            "priority",
            999,
            Path("test.yaml"),
            "Widget",
        )


def test_parse_int_field_raises_on_none_value() -> None:
    with pytest.raises(TypeError, match=r"Widget.*'\?'.*priority.*must be an integer"):
        parse_int_field({"priority": None}, "priority", 999, Path("test.yaml"), "Widget")


def test_parse_int_field_error_includes_entity_kind() -> None:
    with pytest.raises(TypeError, match="Experiment type"):
        parse_int_field(
            {"name": "bench", "priority": "bad"},
            "priority",
            0,
            Path("x.yaml"),
            "Experiment type",
        )

    with pytest.raises(TypeError, match="Methodology tradition"):
        parse_int_field(
            {"name": "ctrl", "priority": "bad"},
            "priority",
            0,
            Path("x.yaml"),
            "Methodology tradition",
        )
