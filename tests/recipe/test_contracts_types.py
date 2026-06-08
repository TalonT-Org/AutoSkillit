"""Tests for the SkillOutput contract dataclass.

Verifies that SkillOutput carries the optional allowed_values field that the
callable verdict routing rules depend on (recipe-routing-deadlock immunity, #3889).
"""

from __future__ import annotations

import pytest

from autoskillit.recipe._contracts_types import SkillOutput

pytestmark = [pytest.mark.layer("recipe"), pytest.mark.small]


def test_skill_output_accepts_allowed_values_kwarg() -> None:
    """SkillOutput must accept an `allowed_values` keyword argument and store it."""
    output = SkillOutput(
        name="committed",
        type="str",
        allowed_values=["false", "true", "regression_detected"],
    )
    assert output.name == "committed"
    assert output.type == "str"
    assert output.allowed_values == ["false", "true", "regression_detected"]


def test_skill_output_allowed_values_defaults_to_empty_list() -> None:
    """SkillOutput must default `allowed_values` to an empty list (backward compat)."""
    output = SkillOutput(name="verdict", type="str")
    assert output.allowed_values == []


def test_skill_output_preserves_order_of_allowed_values() -> None:
    """SkillOutput must preserve the order of allowed_values as supplied."""
    expected = ["z", "a", "m", "b"]
    output = SkillOutput(name="x", type="str", allowed_values=expected)
    assert output.allowed_values == expected
