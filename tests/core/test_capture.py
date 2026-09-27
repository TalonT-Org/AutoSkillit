"""Tests for capture type contracts and resolve_payload_field."""

from __future__ import annotations

from typing import get_args

import pytest

from autoskillit.core.types import (
    CAPTURE_VALID_VALUE_TYPES,
    CaptureEntrySpec,
    CaptureValueType,
    is_capture_value_type,
    resolve_payload_field,
)

pytestmark = [pytest.mark.layer("core"), pytest.mark.small, pytest.mark.feature("fleet")]


class TestResolvePayloadField:
    @pytest.mark.parametrize(
        ("template", "expected"),
        [
            ("${{ result.worktree_path }}", "worktree_path"),
            ("${{ result.pr_url }}", "pr_url"),
            ("${{ result.field_name }}", "field_name"),
        ],
    )
    def test_basic_field_extraction(self, template: str, expected: str) -> None:
        entry = CaptureEntrySpec(from_=template, value_type="string")
        assert resolve_payload_field(entry) == expected

    def test_hyphenated_field_name(self) -> None:
        entry = CaptureEntrySpec(from_="${{ result.worktree-path }}", value_type="string")
        assert resolve_payload_field(entry) == "worktree-path"

    def test_non_result_template_returns_none(self) -> None:
        entry = CaptureEntrySpec(from_="not a template", value_type="string")
        assert resolve_payload_field(entry) is None

    @pytest.mark.parametrize(
        "template",
        [
            "${{result.worktree_path}}",
            "${{  result.worktree_path  }}",
        ],
    )
    def test_whitespace_variations(self, template: str) -> None:
        entry = CaptureEntrySpec(from_=template, value_type="string")
        assert resolve_payload_field(entry) == "worktree_path"

    def test_with_value_type(self) -> None:
        entry = CaptureEntrySpec(from_="${{ result.worktree_path }}", value_type="path")
        assert resolve_payload_field(entry) == "worktree_path"


def test_value_type_is_required() -> None:
    with pytest.raises(TypeError, match="value_type"):
        CaptureEntrySpec(from_="${{ result.x }}")


def test_valid_value_types_match_the_literal() -> None:
    assert CAPTURE_VALID_VALUE_TYPES == frozenset(get_args(CaptureValueType))


@pytest.mark.parametrize("value", sorted(CAPTURE_VALID_VALUE_TYPES))
def test_is_capture_value_type_accepts_declared_types(value: str) -> None:
    assert is_capture_value_type(value)


def test_is_capture_value_type_rejects_unknown_type() -> None:
    assert not is_capture_value_type("integer")
