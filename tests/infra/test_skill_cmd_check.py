"""Unit tests for the skill_cmd_check PreToolUse hook."""

import json
from io import StringIO
from unittest.mock import patch

from autoskillit.hooks.skill_cmd_guard import _looks_like_path, main


def _run_hook(tool_input: dict) -> dict | None:
    """Invoke main() with tool_input as the stdin JSON event.
    Returns parsed JSON output, or None if hook printed nothing.
    """
    event = {"tool_input": tool_input}
    captured = StringIO()
    with patch("sys.stdin", StringIO(json.dumps(event))), patch("sys.stdout", captured):
        try:
            main()
        except SystemExit:
            pass
    output = captured.getvalue().strip()
    return json.loads(output) if output else None


def _decision(result: dict | None) -> str:
    if result is None:
        return "allow"
    return result.get("hookSpecificOutput", {}).get("permissionDecision", "allow")


# ---------------------------------------------------------------------------
# _looks_like_path unit tests
# ---------------------------------------------------------------------------


class TestLooksLikePath:
    def test_slash_prefix(self):
        assert _looks_like_path("/home/user/plan.md") is True

    def test_dotslash_prefix(self):
        assert _looks_like_path("./plan.md") is True

    def test_temp_prefix_no_longer_valid(self):
        assert _looks_like_path("temp/make-plan/plan.md") is False

    def test_autoskillit_prefix(self):
        assert _looks_like_path(".autoskillit/temp/plan.md") is True

    def test_plain_word(self):
        assert _looks_like_path("the") is False

    def test_verified(self):
        assert _looks_like_path("verified") is False

    def test_empty(self):
        assert _looks_like_path("") is False


# ---------------------------------------------------------------------------
# Allow cases
# ---------------------------------------------------------------------------


class TestSkillCmdCheckAllow:
    def test_valid_path_slash_prefix(self):
        result = _run_hook(
            {"skill_command": "/autoskillit:implement-worktree-no-merge /path/to/plan.md"}
        )
        assert _decision(result) == "allow"

    def test_valid_path_autoskillit_temp_prefix(self):
        result = _run_hook(
            {
                "skill_command": "/autoskillit:implement-worktree-no-merge .autoskillit/temp/make-plan/plan.md"  # noqa: E501
            }
        )
        assert _decision(result) == "allow"

    def test_valid_path_dotslash(self):
        result = _run_hook({"skill_command": "/autoskillit:implement-worktree-no-merge ./plan.md"})
        assert _decision(result) == "allow"

    def test_implement_worktree_valid(self):
        result = _run_hook(
            {"skill_command": "/autoskillit:implement-worktree .autoskillit/temp/plan.md"}
        )
        assert _decision(result) == "allow"

    def test_retry_worktree_valid(self):
        result = _run_hook(
            {"skill_command": "/autoskillit:retry-worktree /path/plan.md /path/worktree"}
        )
        assert _decision(result) == "allow"

    def test_resolve_failures_valid(self):
        result = _run_hook(
            {"skill_command": "/autoskillit:resolve-failures /path/worktree /path/plan.md main"}
        )
        assert _decision(result) == "allow"

    def test_non_path_skill_is_allowed_regardless(self):
        """Skills not in PATH_ARG_SKILLS are always allowed — free-form args."""
        result = _run_hook(
            {"skill_command": "/autoskillit:investigate describe the error in logging"}
        )
        assert _decision(result) == "allow"

    def test_make_plan_allowed_free_form(self):
        result = _run_hook(
            {"skill_command": "/autoskillit:make-plan add feature X to the Y system"}
        )
        assert _decision(result) == "allow"

    def test_no_skill_command_key_allows(self):
        result = _run_hook({})
        assert _decision(result) == "allow"

    def test_empty_skill_command_allows(self):
        result = _run_hook({"skill_command": ""})
        assert _decision(result) == "allow"

    def test_no_path_like_tokens_allows(self):
        """Path-arg skill with no path-like tokens at all — could be pasted content."""
        result = _run_hook(
            {"skill_command": "/autoskillit:implement-worktree-no-merge some random free text"}
        )
        assert _decision(result) == "allow"

    def test_malformed_json_stdin_allows(self):
        captured = StringIO()
        with patch("sys.stdin", StringIO("not json at all")), patch("sys.stdout", captured):
            try:
                main()
            except SystemExit:
                pass
        output = captured.getvalue().strip()
        # No deny output emitted on malformed input
        assert output == "" or _decision(json.loads(output) if output else None) == "allow"


# ---------------------------------------------------------------------------
# Deny cases
# ---------------------------------------------------------------------------

# Canonical bug: extra words before path token (matches the bug report exactly)
_CANONICAL_BUG = (
    "/autoskillit:implement-worktree-no-merge the verified plan .autoskillit/temp/rectify/plan.md"
)
_WORKTREE_ABSOLUTE = "/autoskillit:implement-worktree the latest approved /home/user/plans/plan.md"
_RETRY_EXTRA = "/autoskillit:retry-worktree use this plan .autoskillit/temp/plan.md /path/worktree"
_RESOLVE_EXTRA = "/autoskillit:resolve-failures the worktree /path/worktree /path/plan.md main"


class TestSkillCmdCheckDeny:
    def test_canonical_bug_extra_words_before_temp_path(self):
        """The exact failing pattern from the bug report."""
        result = _run_hook({"skill_command": _CANONICAL_BUG})
        assert _decision(result) == "deny"

    def test_deny_message_contains_found_path(self):
        result = _run_hook({"skill_command": _CANONICAL_BUG})
        reason = result["hookSpecificOutput"]["permissionDecisionReason"]
        assert ".autoskillit/temp/rectify/plan.md" in reason

    def test_deny_message_contains_skill_name(self):
        result = _run_hook({"skill_command": _CANONICAL_BUG})
        reason = result["hookSpecificOutput"]["permissionDecisionReason"]
        assert "implement-worktree-no-merge" in reason

    def test_deny_message_suggests_correct_format(self):
        """Denial reason should show the correct skill_command to use."""
        result = _run_hook({"skill_command": _CANONICAL_BUG})
        reason = result["hookSpecificOutput"]["permissionDecisionReason"]
        # Should show the extracted path in the corrected format
        assert (
            "/autoskillit:implement-worktree-no-merge .autoskillit/temp/rectify/plan.md" in reason
        )

    def test_extra_words_before_absolute_path(self):
        result = _run_hook({"skill_command": _WORKTREE_ABSOLUTE})
        assert _decision(result) == "deny"

    def test_retry_worktree_extra_words(self):
        result = _run_hook({"skill_command": _RETRY_EXTRA})
        assert _decision(result) == "deny"

    def test_resolve_failures_extra_words(self):
        result = _run_hook({"skill_command": _RESOLVE_EXTRA})
        assert _decision(result) == "deny"

    def test_hookSpecificOutput_structure(self):
        """Verify the deny response has the correct JSON structure."""
        cmd = "/autoskillit:implement-worktree-no-merge extra .autoskillit/temp/plan.md"
        result = _run_hook({"skill_command": cmd})
        assert "hookSpecificOutput" in result
        hso = result["hookSpecificOutput"]
        assert hso["hookEventName"] == "PreToolUse"
        assert hso["permissionDecision"] == "deny"
        assert "permissionDecisionReason" in hso
