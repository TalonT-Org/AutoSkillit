"""Tests for capture type contracts and resolve_payload_field."""

from __future__ import annotations

import pytest

from autoskillit.core.types import CaptureEntrySpec, resolve_payload_field

pytestmark = [pytest.mark.layer("core"), pytest.mark.small, pytest.mark.feature("fleet")]


class TestResolvePayloadField:
    def test_basic_field_extraction(self) -> None:
        entry = CaptureEntrySpec(from_="${{ result.worktree_path }}")
        assert resolve_payload_field(entry) == "worktree_path"

    def test_pr_url_field_extraction(self) -> None:
        entry = CaptureEntrySpec(from_="${{ result.pr_url }}")
        assert resolve_payload_field(entry) == "pr_url"

    def test_hyphenated_field_name(self) -> None:
        entry = CaptureEntrySpec(from_="${{ result.worktree-path }}")
        assert resolve_payload_field(entry) == "worktree-path"

    def test_non_result_template_returns_none(self) -> None:
        entry = CaptureEntrySpec(from_="not a template")
        assert resolve_payload_field(entry) is None

    def test_whitespace_variations(self) -> None:
        entry = CaptureEntrySpec(from_="${{result.worktree_path}}")
        assert resolve_payload_field(entry) == "worktree_path"

        entry = CaptureEntrySpec(from_="${{  result.worktree_path  }}")
        assert resolve_payload_field(entry) == "worktree_path"

    def test_bare_field_name_in_template(self) -> None:
        entry = CaptureEntrySpec(from_="${{ result.field_name }}")
        assert resolve_payload_field(entry) == "field_name"

    def test_with_value_type(self) -> None:
        entry = CaptureEntrySpec(from_="${{ result.worktree_path }}", value_type="path")
        assert resolve_payload_field(entry) == "worktree_path"
