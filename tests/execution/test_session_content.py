"""Tests for session content validation and token normalization."""

from __future__ import annotations

import pytest

from autoskillit.execution.session._session_content import (
    _check_session_content,
    _evaluate_content_state,
    _strip_markdown_from_tokens,
)
from autoskillit.execution.session._session_model import ClaudeSessionResult, ContentState

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


_PREP_RESULT = "prep_path = /tmp/plan.md\nselected_lenses = dev\nlens_context_paths = /tmp/ctx.md"

_PREP_PATTERNS = [
    r"prep_path\s*=\s*/.+",
    r"selected_lenses\s*=\s*\S+",
    r"lens_context_paths\s*=\s*/.+",
]


class TestCheckSessionContent:
    """Tests for _check_session_content."""

    def test_patterns_present_marker_absent_returns_true(self) -> None:
        session = ClaudeSessionResult(
            subtype="success",
            is_error=False,
            result=_PREP_RESULT,
            session_id="s1",
        )
        result = _check_session_content(
            session,
            completion_marker="%%ORDER_UP%%",
            expected_output_patterns=_PREP_PATTERNS,
        )
        assert result is True

    def test_patterns_absent_marker_absent_returns_false(self) -> None:
        session = ClaudeSessionResult(
            subtype="success",
            is_error=False,
            result="Some work but no tokens",
            session_id="s1",
        )
        result = _check_session_content(
            session,
            completion_marker="%%ORDER_UP%%",
            expected_output_patterns=_PREP_PATTERNS,
        )
        assert result is False

    def test_no_patterns_configured_marker_absent_returns_false(self) -> None:
        session = ClaudeSessionResult(
            subtype="success",
            is_error=False,
            result=_PREP_RESULT,
            session_id="s1",
        )
        result = _check_session_content(
            session,
            completion_marker="%%ORDER_UP%%",
            expected_output_patterns=(),
        )
        assert result is False

    def test_partial_patterns_marker_absent_returns_false(self) -> None:
        session = ClaudeSessionResult(
            subtype="success",
            is_error=False,
            result="prep_path = /tmp/plan.md",
            session_id="s1",
        )
        result = _check_session_content(
            session,
            completion_marker="%%ORDER_UP%%",
            expected_output_patterns=_PREP_PATTERNS,
        )
        assert result is False

    def test_patterns_present_marker_present_returns_true(self) -> None:
        session = ClaudeSessionResult(
            subtype="success",
            is_error=False,
            result=_PREP_RESULT + "\n%%ORDER_UP%%",
            session_id="s1",
        )
        result = _check_session_content(
            session,
            completion_marker="%%ORDER_UP%%",
            expected_output_patterns=_PREP_PATTERNS,
        )
        assert result is True


class TestEvaluateContentState:
    """Tests for _evaluate_content_state."""

    def test_marker_absent_all_patterns_match_returns_marker_absent_contract_met(self) -> None:
        session = ClaudeSessionResult(
            subtype="success",
            is_error=False,
            result=_PREP_RESULT,
            session_id="s1",
        )
        state = _evaluate_content_state(
            session,
            "%%ORDER_UP%%",
            _PREP_PATTERNS,
        )
        assert state == ContentState.MARKER_ABSENT_CONTRACT_MET

    def test_marker_absent_no_patterns_returns_absent(self) -> None:
        session = ClaudeSessionResult(
            subtype="success",
            is_error=False,
            result=_PREP_RESULT,
            session_id="s1",
        )
        state = _evaluate_content_state(session, "%%ORDER_UP%%", [])
        assert state == ContentState.ABSENT

    def test_marker_absent_patterns_fail_returns_absent(self) -> None:
        session = ClaudeSessionResult(
            subtype="success",
            is_error=False,
            result="Some work without matching tokens",
            session_id="s1",
        )
        state = _evaluate_content_state(
            session,
            "%%ORDER_UP%%",
            _PREP_PATTERNS,
        )
        assert state == ContentState.ABSENT

    def test_marker_present_patterns_fail_returns_contract_violation(self) -> None:
        session = ClaudeSessionResult(
            subtype="success",
            is_error=False,
            result="Some work done. %%ORDER_UP%%",
            session_id="s1",
        )
        state = _evaluate_content_state(
            session,
            "%%ORDER_UP%%",
            _PREP_PATTERNS,
        )
        assert state == ContentState.CONTRACT_VIOLATION
