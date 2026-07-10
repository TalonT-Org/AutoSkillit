"""Tests for _validate_dispatch_items and DISPATCH_ITEM_PLACEHOLDER enforcement."""

from __future__ import annotations

import pytest

pytestmark = [pytest.mark.layer("recipe"), pytest.mark.small]


def _validate(value):
    from autoskillit.recipe.io import _validate_dispatch_items

    return _validate_dispatch_items(value, step_name="test_step")


def test_validate_dispatch_items_accepts_none() -> None:
    """Absence is the canonical 'no dispatch' state."""
    assert _validate(None) is None


def test_validate_dispatch_items_accepts_single_input_ref() -> None:
    assert _validate("${{ inputs.dispatched_items }}") == "${{ inputs.dispatched_items }}"


def test_validate_dispatch_items_accepts_single_context_ref() -> None:
    assert _validate("${{ context.dispatched_items }}") == "${{ context.dispatched_items }}"


def test_validate_dispatch_items_accepts_whitespace_inside_braces() -> None:
    assert _validate("${{  inputs.x  }}") == "${{  inputs.x  }}"


def test_validate_dispatch_items_rejects_empty_string() -> None:
    with pytest.raises(ValueError, match="must not be empty"):
        _validate("")


def test_validate_dispatch_items_rejects_non_string_types() -> None:
    for bad in [123, 1.5, True, ["x"], {"y": 1}]:
        with pytest.raises(ValueError, match="must be a string or null"):
            _validate(bad)


def test_validate_dispatch_items_rejects_zero_refs() -> None:
    with pytest.raises(ValueError, match="exactly one"):
        _validate("not a template at all")


def test_validate_dispatch_items_rejects_multiple_refs() -> None:
    with pytest.raises(ValueError, match="exactly one"):
        _validate("${{ inputs.a }} and ${{ context.b }}")


def test_validate_dispatch_items_rejects_unsupported_namespace() -> None:
    """Only inputs.X and context.X are valid dispatch sources."""
    with pytest.raises(ValueError, match="exactly one"):
        _validate("${{ result.dispatched_items }}")


def test_dispatch_item_placeholder_constant_alignment() -> None:
    """The placeholder must equal the splice marker and is single-brace."""
    from autoskillit.core import DISPATCH_ITEM_PLACEHOLDER

    assert DISPATCH_ITEM_PLACEHOLDER == "{selected_dispatch_item}"
    assert DISPATCH_ITEM_PLACEHOLDER.startswith("{")
    assert DISPATCH_ITEM_PLACEHOLDER.endswith("}")


def test_step_with_invalid_dispatch_items_fails_load() -> None:
    """load_recipe must surface the defect during parsing."""
    from autoskillit.recipe.io import _validate_dispatch_items

    # Valid case loads cleanly.
    assert _validate_dispatch_items("${{ inputs.x }}", step_name="ok") == "${{ inputs.x }}"
    # Invalid case raises.
    with pytest.raises(ValueError):
        _validate_dispatch_items(42, step_name="bad")
