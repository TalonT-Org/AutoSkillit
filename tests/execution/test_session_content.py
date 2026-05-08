"""Tests for session content validation and token normalization."""

from __future__ import annotations

import pytest

from autoskillit.execution.session._session_content import _strip_markdown_from_tokens

pytestmark = [pytest.mark.layer("execution"), pytest.mark.small]


class TestStripMarkdownFromTokens:
    """_strip_markdown_from_tokens normalizes model output decorators to canonical form."""

    def test_bold_equals_lowercase(self) -> None:
        """**plan_path** = /abs/path → plan_path = /abs/path"""
        result = _strip_markdown_from_tokens("**plan_path** = /abs/path/plan.md")
        assert result == "plan_path = /abs/path/plan.md"

    def test_bold_equals_uppercase_key_lowercased(self) -> None:
        """**Plan_Path** = /abs/path → plan_path = /abs/path"""
        result = _strip_markdown_from_tokens("**Plan_Path** = /abs/path/plan.md")
        assert result == "plan_path = /abs/path/plan.md"

    def test_italic_equals(self) -> None:
        """*plan_path* = /abs/path → plan_path = /abs/path"""
        result = _strip_markdown_from_tokens("*plan_path* = /abs/path/plan.md")
        assert result == "plan_path = /abs/path/plan.md"

    def test_bold_colon_separator(self) -> None:
        """**Plan_Path:** /abs/path → plan_path = /abs/path"""
        result = _strip_markdown_from_tokens("**Plan_Path:** /abs/path/plan.md")
        assert result == "plan_path = /abs/path/plan.md"

    def test_backtick_equals(self) -> None:
        """`plan_path` = /abs/path → plan_path = /abs/path"""
        result = _strip_markdown_from_tokens("`plan_path` = /abs/path/plan.md")
        assert result == "plan_path = /abs/path/plan.md"

    def test_backtick_equals_uppercase_key_lowercased(self) -> None:
        """`Plan_Path` = /abs/path → plan_path = /abs/path"""
        result = _strip_markdown_from_tokens("`Plan_Path` = /abs/path/plan.md")
        assert result == "plan_path = /abs/path/plan.md"

    def test_canonical_no_change(self) -> None:
        """Already-canonical token is unchanged."""
        result = _strip_markdown_from_tokens("plan_path = /abs/path/plan.md")
        assert result == "plan_path = /abs/path/plan.md"

    def test_non_adjacent_bold_untouched(self) -> None:
        """Bold decorators not adjacent to = or : are left unchanged."""
        result = _strip_markdown_from_tokens("Some text with **bold** words here")
        assert result == "Some text with **bold** words here"

    def test_multiple_tokens_all_normalized(self) -> None:
        """Multiple decorated tokens in one string are all normalized."""
        result = _strip_markdown_from_tokens(
            "**worktree_path** = /tmp/wt\n`branch_name` = impl-xyz\n*status* = running\n"
        )
        assert "worktree_path = /tmp/wt" in result
        assert "branch_name = impl-xyz" in result
        assert "status = running" in result

    def test_worktree_path_bold_colon_variant(self) -> None:
        """The colon-decorated variant that caused #1716 contract failures."""
        result = _strip_markdown_from_tokens("**worktree_path:** /tmp/wt")
        assert result == "worktree_path = /tmp/wt"
