"""Tests for the remove_clone_guard PreToolUse hook."""

import io
import json
from contextlib import redirect_stdout
from unittest.mock import patch


def _run_hook(event: dict) -> str:
    """Run main() with the given event JSON, return captured stdout."""
    from autoskillit.hooks.remove_clone_guard import main

    with patch("sys.stdin", io.StringIO(json.dumps(event))):
        buf = io.StringIO()
        with redirect_stdout(buf):
            try:
                main()
            except SystemExit:
                pass
        return buf.getvalue()


def test_deny_when_keep_false():
    out = _run_hook({"tool_input": {"keep": "false", "clone_path": "/tmp/my-clone"}})
    data = json.loads(out)
    assert data["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert "/tmp/my-clone" in data["hookSpecificOutput"]["permissionDecisionReason"]
    assert "rm -rf" in data["hookSpecificOutput"]["permissionDecisionReason"]


def test_deny_when_keep_missing():
    """No keep param defaults to blocked."""
    out = _run_hook({"tool_input": {"clone_path": "/tmp/clone"}})
    data = json.loads(out)
    assert data["hookSpecificOutput"]["permissionDecision"] == "deny"


def test_deny_when_keep_is_garbage():
    out = _run_hook({"tool_input": {"keep": "yes", "clone_path": "/tmp/clone"}})
    data = json.loads(out)
    assert data["hookSpecificOutput"]["permissionDecision"] == "deny"


def test_approve_silently_when_keep_true():
    """keep=true is a no-op; hook exits silently (empty stdout)."""
    out = _run_hook({"tool_input": {"keep": "true", "clone_path": "/tmp/clone"}})
    assert out.strip() == ""


def test_approve_on_malformed_json():
    """Any parse error silently approves."""
    from autoskillit.hooks.remove_clone_guard import main

    with patch("sys.stdin", io.StringIO("not-json")):
        buf = io.StringIO()
        with redirect_stdout(buf):
            try:
                main()
            except SystemExit:
                pass
        assert buf.getvalue().strip() == ""
