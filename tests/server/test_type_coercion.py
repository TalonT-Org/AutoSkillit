"""Unit tests for coerce_override_value and _validate_override_types.

Pure function tests — no server fixtures, no monkeypatching. These verify the
type gate's coercion semantics in isolation.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from autoskillit.server.tools._type_coercion import (
    OverrideCoercionError,
    _validate_override_types,
    coerce_override_value,
)

pytestmark = [pytest.mark.layer("server"), pytest.mark.small]


# ---------------------------------------------------------------------------
# None / string / optional_string
# ---------------------------------------------------------------------------


def test_coerce_returns_value_for_none_type() -> None:
    assert coerce_override_value("anything", None) == "anything"
    assert coerce_override_value("", None) == ""


def test_coerce_accepts_empty_for_optional_string() -> None:
    """optional_string accepts empty values (worktree paths may be unset)."""
    assert coerce_override_value("", "optional_string") == ""


def test_coerce_accepts_string_for_string_type() -> None:
    assert coerce_override_value("hello", "string") == "hello"


# ---------------------------------------------------------------------------
# path
# ---------------------------------------------------------------------------


def test_coerce_rejects_empty_for_path() -> None:
    with pytest.raises(OverrideCoercionError, match="path value must be non-empty"):
        coerce_override_value("", "path")


def test_coerce_accepts_non_empty_for_path() -> None:
    assert coerce_override_value("/tmp/x", "path") == "/tmp/x"


# ---------------------------------------------------------------------------
# integer
# ---------------------------------------------------------------------------


def test_coerce_accepts_valid_integer() -> None:
    assert coerce_override_value("42", "integer") == "42"
    assert coerce_override_value("-7", "integer") == "-7"
    assert coerce_override_value("0", "integer") == "0"


def test_coerce_rejects_invalid_integer() -> None:
    with pytest.raises(OverrideCoercionError, match="integer value must parse as int"):
        coerce_override_value("abc", "integer")
    with pytest.raises(OverrideCoercionError, match="integer value must parse as int"):
        coerce_override_value("3.14", "integer")


# ---------------------------------------------------------------------------
# boolean
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "value",
    ["true", "false", "1", "0", "yes", "no", "TRUE", "FALSE", "Yes", "No"],
)
def test_coerce_accepts_each_boolean_value(value: str) -> None:
    assert coerce_override_value(value, "boolean") == value


def test_coerce_rejects_invalid_boolean() -> None:
    with pytest.raises(OverrideCoercionError, match="boolean value must be one of"):
        coerce_override_value("maybe", "boolean")


# ---------------------------------------------------------------------------
# list
# ---------------------------------------------------------------------------


def test_coerce_accepts_valid_list() -> None:
    assert coerce_override_value("[1,2,3]", "list") == "[1,2,3]"
    assert coerce_override_value('["a","b"]', "list") == '["a","b"]'
    assert coerce_override_value("[]", "list") == "[]"


def test_coerce_rejects_non_list_json() -> None:
    with pytest.raises(OverrideCoercionError, match="list value must decode to a JSON array"):
        coerce_override_value('{"a":1}', "list")


def test_coerce_rejects_invalid_json_for_list() -> None:
    with pytest.raises(OverrideCoercionError, match="list value must be valid JSON array"):
        coerce_override_value("not-json", "list")


# ---------------------------------------------------------------------------
# dict
# ---------------------------------------------------------------------------


def test_coerce_accepts_valid_dict() -> None:
    assert coerce_override_value('{"a":1}', "dict") == '{"a":1}'
    assert coerce_override_value("{}", "dict") == "{}"


def test_coerce_rejects_non_dict_json() -> None:
    with pytest.raises(OverrideCoercionError, match="dict value must decode to a JSON object"):
        coerce_override_value("[1,2]", "dict")


def test_coerce_rejects_invalid_json_for_dict() -> None:
    with pytest.raises(OverrideCoercionError, match="dict value must be valid JSON object"):
        coerce_override_value("not-json", "dict")


# ---------------------------------------------------------------------------
# Non-string rejection
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("value", [123, None, 1.5, True, False, [], {}])
def test_coerce_rejects_non_string_value(value: object) -> None:
    with pytest.raises(OverrideCoercionError, match="override value must be a string"):
        coerce_override_value(value, "integer")


# ---------------------------------------------------------------------------
# absolute_path
# ---------------------------------------------------------------------------


def test_coerce_accepts_absolute_path() -> None:
    assert coerce_override_value("/tmp/foo", "absolute_path") == "/tmp/foo"


def test_coerce_rejects_empty_absolute_path() -> None:
    with pytest.raises(OverrideCoercionError, match="absolute_path value must be non-empty"):
        coerce_override_value("", "absolute_path")


# ---------------------------------------------------------------------------
# worktree_relative_path
# ---------------------------------------------------------------------------


def test_coerce_accepts_worktree_relative_path_empty() -> None:
    """worktree_relative_path allows empty values (worktree root == project root)."""
    assert coerce_override_value("", "worktree_relative_path") == ""


def test_coerce_accepts_worktree_relative_path_value() -> None:
    assert coerce_override_value("subdir/recipe.yaml", "worktree_relative_path") == (
        "subdir/recipe.yaml"
    )


# ---------------------------------------------------------------------------
# Defense in depth — unknown declared type
# ---------------------------------------------------------------------------


def test_coerce_rejects_unknown_declared_type() -> None:
    """Defense in depth: RecipeIngredient.__post_init__ rejects unknown types at
    parse time, but runtime-constructed instances can still reach this branch."""
    with pytest.raises(OverrideCoercionError, match="unknown declared type"):
        coerce_override_value("value", "mystery_type")


# ---------------------------------------------------------------------------
# _validate_override_types (Tier-2 gate)
# ---------------------------------------------------------------------------


def _mock_recipe(ingredients: dict[str, str | None]) -> SimpleNamespace:
    return SimpleNamespace(
        ingredients={name: SimpleNamespace(type=t) for name, t in ingredients.items()}
    )


def test_validate_override_types_returns_none_on_empty_overrides() -> None:
    recipe = _mock_recipe({"count": "integer"})
    assert _validate_override_types(None, recipe) is None
    assert _validate_override_types({}, recipe) is None


def test_validate_override_types_passes_through_valid_overrides() -> None:
    recipe = _mock_recipe({"count": "integer", "name": "string"})
    assert _validate_override_types({"count": "42", "name": "demo"}, recipe) is None


def test_validate_override_types_returns_envelope_on_failure() -> None:
    recipe = _mock_recipe({"count": "integer"})
    result = _validate_override_types({"count": "abc"}, recipe)
    assert result is not None
    parsed = json.loads(result)
    assert parsed["success"] is False
    assert parsed["stage"] == "ingredient_type_validation"
    assert parsed["retriable"] is False
    assert "count" in parsed["error"]
    assert "count" in parsed["user_visible_message"]


def test_validate_override_types_skips_unknown_keys() -> None:
    """Unknown override keys (not in recipe.ingredients) are delegated to the
    caller via _check_override_keys — _validate_override_types skips them."""
    recipe = _mock_recipe({"count": "integer"})
    # Unknown key not in recipe — must NOT raise.
    assert _validate_override_types({"unknown_key": "anything"}, recipe) is None


def test_validate_override_types_skips_untyped_ingredients() -> None:
    """Ingredients with type=None are not validated."""
    recipe = _mock_recipe({"name": None})  # type=None
    # Anything goes for untyped ingredient.
    assert _validate_override_types({"name": "anything-at-all"}, recipe) is None
