"""Tests for the skill_command_guard PreToolUse hook."""

import io
import json
from contextlib import redirect_stdout
from unittest.mock import patch


def _run_hook(event: dict) -> str:
    """Run main() with the given event JSON, return captured stdout."""
    from autoskillit.hooks.skill_command_guard import main

    with patch("sys.stdin", io.StringIO(json.dumps(event))):
        buf = io.StringIO()
        with redirect_stdout(buf):
            try:
                main()
            except SystemExit:
                pass
        return buf.getvalue()


def test_deny_when_no_slash():
    """skill_command is free-form prose → deny."""
    out = _run_hook(
        {"tool_input": {"skill_command": "Fix the authentication bug in main.py", "cwd": "/tmp"}}
    )
    data = json.loads(out)
    assert data["hookSpecificOutput"]["permissionDecision"] == "deny"


def test_deny_when_empty_string():
    """Empty skill_command → deny."""
    out = _run_hook({"tool_input": {"skill_command": "", "cwd": "/tmp"}})
    data = json.loads(out)
    assert data["hookSpecificOutput"]["permissionDecision"] == "deny"


def test_deny_when_whitespace_only():
    """Whitespace-only → deny (strip before check)."""
    out = _run_hook({"tool_input": {"skill_command": "   ", "cwd": "/tmp"}})
    data = json.loads(out)
    assert data["hookSpecificOutput"]["permissionDecision"] == "deny"


def test_deny_reason_instructs_slash():
    """Deny reason must mention the slash requirement and give an example."""
    out = _run_hook({"tool_input": {"skill_command": "Do something", "cwd": "/tmp"}})
    data = json.loads(out)
    reason = data["hookSpecificOutput"]["permissionDecisionReason"]
    assert "/" in reason
    assert "/autoskillit:" in reason


def test_approve_autoskillit_command():
    """/autoskillit:investigate → approve (empty stdout)."""
    out = _run_hook(
        {"tool_input": {"skill_command": "/autoskillit:investigate the bug", "cwd": "/tmp"}}
    )
    assert out.strip() == ""


def test_approve_bare_slash_command():
    """/audit-arch (local project skill) → approve."""
    out = _run_hook({"tool_input": {"skill_command": "/audit-arch", "cwd": "/tmp"}})
    assert out.strip() == ""


def test_approve_slash_with_leading_whitespace():
    """Leading whitespace stripped before check."""
    out = _run_hook({"tool_input": {"skill_command": "  /autoskillit:investigate", "cwd": "/tmp"}})
    assert out.strip() == ""


def test_approve_multiline_slash_command():
    """/autoskillit:report-bug with multiline error context → approve."""
    out = _run_hook(
        {
            "tool_input": {
                "skill_command": "/autoskillit:report-bug\n\nerror context here",
                "cwd": "/tmp",
            }
        }
    )
    assert out.strip() == ""


def test_approve_on_malformed_json():
    """Malformed stdin → fail-open (empty stdout, no crash)."""
    from autoskillit.hooks.skill_command_guard import main

    with patch("sys.stdin", io.StringIO("not-json")):
        buf = io.StringIO()
        with redirect_stdout(buf):
            try:
                main()
            except SystemExit:
                pass
        assert buf.getvalue().strip() == ""


def test_approve_when_skill_command_key_missing():
    """tool_input has no skill_command key → fail-open."""
    out = _run_hook({"tool_input": {"cwd": "/tmp"}})
    assert out.strip() == ""


def test_hook_uses_skill_command_prefix_constant():
    """Hook must import SKILL_COMMAND_PREFIX from autoskillit.core, not hardcode '/'."""
    import inspect

    import autoskillit.hooks.skill_command_guard as mod

    src = inspect.getsource(mod)
    assert "from autoskillit.core import" in src
    assert "SKILL_COMMAND_PREFIX" in src


# CC1-1
def test_unexpected_error_denies_not_approves():
    """CC-1: An unexpected exception (e.g., AttributeError from a bug) must
    produce a deny, NOT a silent approve. This ensures the security gate is
    fail-closed for programming errors, not fail-open."""
    from contextlib import redirect_stdout
    from unittest.mock import patch

    import autoskillit.hooks.skill_command_guard as guard_mod

    # Make json.loads raise AttributeError (simulates a programming bug)
    with patch.object(guard_mod.json, "loads", side_effect=AttributeError("bug")):
        buf = io.StringIO()
        with patch("sys.stdin", io.StringIO('{"tool_input":{}}')):
            with redirect_stdout(buf):
                try:
                    guard_mod.main()
                except SystemExit:
                    pass
    output = buf.getvalue().strip()
    assert output != "", "Unexpected error must produce output (deny), not silent approve"
    data = json.loads(output)
    assert data["hookSpecificOutput"]["permissionDecision"] == "deny"
