"""Tests for grep_pattern_lint_guard.py — PreToolUse hook for Grep tool pattern syntax."""

import json
from io import StringIO
from unittest.mock import patch

from autoskillit.hooks.grep_pattern_lint_guard import main


def _run_hook(tool_name: str, pattern: str) -> dict | None:
    """Run the guard with a synthetic PreToolUse event; return parsed stdout or None."""
    event = {"tool_name": tool_name, "tool_input": {"pattern": pattern}}
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


def _reason(result: dict | None) -> str:
    return result.get("hookSpecificOutput", {}).get("permissionDecisionReason", "")


# --- Core deny cases ---


def test_bre_alternation_single_is_denied():
    """Single \\| alternation operator must be denied."""
    result = _run_hook("Grep", r"foo\|bar")
    assert _decision(result) == "deny"


def test_bre_alternation_multiple_is_denied():
    """Multiple \\| operators in one pattern must be denied."""
    result = _run_hook("Grep", r"foo\|bar\|baz")
    assert _decision(result) == "deny"


def test_bre_alternation_in_complex_pattern_is_denied():
    """\\| embedded in a longer regex must be denied."""
    result = _run_hook("Grep", r"def.*generate\|def.*regenerate\|build.*contract")
    assert _decision(result) == "deny"


# --- Allow cases ---


def test_plain_pipe_alternation_is_allowed():
    """Bare | alternation (ripgrep ERE) must be allowed."""
    result = _run_hook("Grep", r"foo|bar")
    assert _decision(result) == "allow"


def test_empty_pattern_is_allowed():
    """Empty pattern must be allowed (no \\| present)."""
    result = _run_hook("Grep", "")
    assert _decision(result) == "allow"


def test_pattern_without_alternation_is_allowed():
    """Plain word pattern without any | must be allowed."""
    result = _run_hook("Grep", r"def.*generate")
    assert _decision(result) == "allow"


# --- Non-Grep tool passthrough ---


def test_bash_tool_passthrough():
    """Hook must not fire for the Bash tool (guard is Grep-specific)."""
    result = _run_hook("Bash", r"grep 'foo\|bar'")
    assert _decision(result) == "allow"


def test_read_tool_passthrough():
    """Hook must not fire for Read tool."""
    result = _run_hook("Read", r"some\|pattern")
    assert _decision(result) == "allow"


# --- Deny message quality ---


def test_deny_reason_includes_corrected_pattern():
    """permissionDecisionReason must include the corrected pattern with | replacing \\|."""
    result = _run_hook("Grep", r"foo\|bar")
    reason = _reason(result)
    assert "foo|bar" in reason


def test_deny_reason_explains_ripgrep_syntax():
    """permissionDecisionReason must mention ripgrep or ERE/alternation context."""
    result = _run_hook("Grep", r"foo\|bar")
    reason = _reason(result)
    assert (
        "ripgrep" in reason.lower() or "alternation" in reason.lower() or "ere" in reason.lower()
    )


def test_deny_reason_for_multiple_replacements():
    """All \\| occurrences must appear corrected in the reason."""
    result = _run_hook("Grep", r"foo\|bar\|baz")
    reason = _reason(result)
    assert "foo|bar|baz" in reason


# --- Malformed input fail-open ---


def test_malformed_json_falls_through():
    """Non-JSON stdin must not crash — hook exits silently (allow)."""
    captured = StringIO()
    with patch("sys.stdin", StringIO("not json")), patch("sys.stdout", captured):
        try:
            main()
        except SystemExit:
            pass
    assert captured.getvalue().strip() == ""


def test_missing_pattern_field_falls_through():
    """tool_input without pattern key must not deny (allow)."""
    event = {"tool_name": "Grep", "tool_input": {}}
    captured = StringIO()
    with patch("sys.stdin", StringIO(json.dumps(event))), patch("sys.stdout", captured):
        try:
            main()
        except SystemExit:
            pass
    result = captured.getvalue().strip()
    output = json.loads(result) if result else None
    assert _decision(output) == "allow"
