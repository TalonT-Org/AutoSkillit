"""Tests for session content validation and token normalization."""

from __future__ import annotations

import pytest

from autoskillit.execution.session._session_content import (
    _check_expected_patterns,
    _check_session_content,
    _evaluate_content_state,
    _normalize_model_output,
)
from autoskillit.execution.session._session_model import ClaudeSessionResult, ContentState

pytestmark = [pytest.mark.layer("execution"), pytest.mark.small]


class TestNormalizeModelOutput:
    """_normalize_model_output normalizes model output decorators to canonical form."""

    def test_bold_equals_lowercase(self) -> None:
        """**plan_path** = /abs/path → plan_path = /abs/path"""
        result = _normalize_model_output("**plan_path** = /abs/path/plan.md")
        assert result == "plan_path = /abs/path/plan.md"

    def test_bold_equals_uppercase_key_lowercased(self) -> None:
        """**Plan_Path** = /abs/path → plan_path = /abs/path"""
        result = _normalize_model_output("**Plan_Path** = /abs/path/plan.md")
        assert result == "plan_path = /abs/path/plan.md"

    def test_italic_equals(self) -> None:
        """*plan_path* = /abs/path → plan_path = /abs/path"""
        result = _normalize_model_output("*plan_path* = /abs/path/plan.md")
        assert result == "plan_path = /abs/path/plan.md"

    def test_bold_colon_separator(self) -> None:
        """**Plan_Path:** /abs/path → plan_path = /abs/path"""
        result = _normalize_model_output("**Plan_Path:** /abs/path/plan.md")
        assert result == "plan_path = /abs/path/plan.md"

    def test_backtick_equals(self) -> None:
        """`plan_path` = /abs/path → plan_path = /abs/path"""
        result = _normalize_model_output("`plan_path` = /abs/path/plan.md")
        assert result == "plan_path = /abs/path/plan.md"

    def test_backtick_equals_uppercase_key_lowercased(self) -> None:
        """`Plan_Path` = /abs/path → plan_path = /abs/path"""
        result = _normalize_model_output("`Plan_Path` = /abs/path/plan.md")
        assert result == "plan_path = /abs/path/plan.md"

    def test_canonical_no_change(self) -> None:
        """Already-canonical token is unchanged."""
        result = _normalize_model_output("plan_path = /abs/path/plan.md")
        assert result == "plan_path = /abs/path/plan.md"

    def test_non_adjacent_bold_untouched(self) -> None:
        """Bold decorators not adjacent to = or : are left unchanged."""
        result = _normalize_model_output("Some text with **bold** words here")
        assert result == "Some text with **bold** words here"

    def test_multiple_tokens_all_normalized(self) -> None:
        """Multiple decorated tokens in one string are all normalized."""
        result = _normalize_model_output(
            "**worktree_path** = /tmp/wt\n`branch_name` = impl-xyz\n*status* = running\n"
        )
        assert "worktree_path = /tmp/wt" in result
        assert "branch_name = impl-xyz" in result
        assert "status = running" in result

    def test_worktree_path_bold_colon_variant(self) -> None:
        """The colon-decorated variant that caused #1716 contract failures."""
        result = _normalize_model_output("**worktree_path:** /tmp/wt")
        assert result == "worktree_path = /tmp/wt"

    def test_hr_split_open_delimiter(self) -> None:
        """HR on its own line followed by token name rejoins into full delimiter."""
        assert _check_expected_patterns(
            "Section content\n\n---\npipeline-health-result---\n\n%%ORDER_UP::abc%%",
            ["---pipeline-health-result---"],
        )

    def test_hr_split_double_newline_delimiter(self) -> None:
        """Double-newline between HR and token name is still collapsed."""
        assert _check_expected_patterns(
            "Content\n\n---\n\npipeline-health-result---\n%%ORDER_UP::abc%%",
            ["---pipeline-health-result---"],
        )

    def test_inline_delimiter_no_regression(self) -> None:
        """Inline delimiter on same line as other text continues to match."""
        assert _check_expected_patterns(
            "Result: ---pipeline-health-result---\n%%ORDER_UP::abc%%",
            ["---pipeline-health-result---"],
        )

    def test_bold_wrapped_delimiter(self) -> None:
        """Bold-wrapped delimiter token is stripped and matched."""
        assert _check_expected_patterns(
            "**---pipeline-health-result---**\n%%ORDER_UP::abc%%",
            ["---pipeline-health-result---"],
        )

    def test_backtick_wrapped_delimiter(self) -> None:
        """Backtick-wrapped delimiter token is stripped and matched."""
        assert _check_expected_patterns(
            "`---pipeline-health-result---`\n%%ORDER_UP::abc%%",
            ["---pipeline-health-result---"],
        )

    def test_multiple_delimiters_mixed_hr_split_and_inline(self) -> None:
        """Open delimiter HR-split and close delimiter inline both match."""
        assert _check_expected_patterns(
            "---\nbug-fingerprint---\nfp-001\n---/bug-fingerprint---\n%%ORDER_UP::abc%%",
            ["---bug-fingerprint---", "---/bug-fingerprint---"],
        )

    def test_hr_split_close_delimiter_with_slash(self) -> None:
        """Close delimiter HR-split (with / prefix) is collapsed and matched."""
        assert _check_expected_patterns(
            "---bug-fingerprint---\nfp-001\n---\n/bug-fingerprint---\n%%ORDER_UP::abc%%",
            ["---bug-fingerprint---", "---/bug-fingerprint---"],
        )

    def test_italic_colon_dedicated(self) -> None:
        """Italic-colon variant is normalized and the pattern matches."""
        assert _check_expected_patterns(
            "*worktree_path*: /tmp/worktrees/impl-foo\n%%ORDER_UP%%",
            [r"worktree_path\s*=\s*/.+"],
        )

    def test_invalid_regex_returns_false(self) -> None:
        """Invalid regex pattern returns False without raising."""
        assert _check_expected_patterns("any text", ["[invalid regex"]) is False


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


class TestNormalizeModelOutputCodeFence:
    """Tests for Stage 0 (code-fence stripping) and Stage 2.5 (backtick value strip)."""

    def test_code_fence_stripped_token_visible(self) -> None:
        raw = "```\nworktree_path = /tmp/wt\n```"
        result = _normalize_model_output(raw)
        assert "worktree_path = /tmp/wt" in result

    def test_code_fence_with_language_tag_stripped(self) -> None:
        raw = "```markdown\nworktree_path = /tmp/wt\n```"
        result = _normalize_model_output(raw)
        assert "worktree_path = /tmp/wt" in result

    def test_code_fence_preserves_non_token_content(self) -> None:
        raw = "```\nsome content\nworktree_path = /tmp/wt\nmore content\n```"
        result = _normalize_model_output(raw)
        assert "some content" in result
        assert "worktree_path = /tmp/wt" in result
        assert "more content" in result

    def test_backtick_wrapped_value_stripped(self) -> None:
        result = _normalize_model_output("worktree_path = `/tmp/wt-val`")
        assert "worktree_path = /tmp/wt-val" in result

    def test_backtick_value_not_applied_to_key(self) -> None:
        result = _normalize_model_output("`worktree_path` = /tmp/wt")
        assert "worktree_path = /tmp/wt" in result
