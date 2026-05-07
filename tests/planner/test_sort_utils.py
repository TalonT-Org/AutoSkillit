import pytest

from autoskillit.planner._sort_utils import _natural_sort_key

pytestmark = [pytest.mark.layer("planner"), pytest.mark.small, pytest.mark.feature("planner")]


def test_natural_sort_key_numeric_segments():
    items = ["P1-A10", "P1-A2", "P1-A1", "P1-A20"]
    result = sorted(items, key=_natural_sort_key)
    assert result == ["P1-A1", "P1-A2", "P1-A10", "P1-A20"]


def test_natural_sort_key_pure_text():
    assert _natural_sort_key("abc") == ["abc"]


def test_natural_sort_key_pure_digits():
    key = _natural_sort_key("42")
    assert any(isinstance(tok, int) for tok in key)


def test_natural_sort_key_empty_string():
    assert _natural_sort_key("") == [""]
