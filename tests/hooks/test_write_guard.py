"""Tests for write_guard.py PreToolUse hook."""

from __future__ import annotations

import io
import json
import sys
from contextlib import redirect_stdout
from unittest.mock import patch

import pytest

pytestmark = [pytest.mark.layer("infra"), pytest.mark.small]


def _build_event(tool_name: str, file_path: str) -> dict:
    return {"tool_name": tool_name, "tool_input": {"file_path": file_path}}


def _run_hook(event: dict | str) -> str:
    from autoskillit.hooks.write_guard import main

    stdin_text = json.dumps(event) if isinstance(event, dict) else event
    buf = io.StringIO()
    with (
        patch("sys.stdin", io.StringIO(stdin_text)),
        redirect_stdout(buf),
    ):
        try:
            main()
        except SystemExit:
            pass
    return buf.getvalue()


class TestWriteGuardNoEnv:
    def test_no_env_var_allows_all_writes(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.delenv("AUTOSKILLIT_ALLOWED_WRITE_PREFIX", raising=False)
        result = _run_hook(_build_event("Write", "/src/foo.py"))
        assert result == ""

    def test_no_json_allows_when_no_prefix(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.delenv("AUTOSKILLIT_ALLOWED_WRITE_PREFIX", raising=False)
        result = _run_hook("not json at all")
        assert result == ""


class TestWriteGuardWithPrefix:
    PREFIX = "/clone/.autoskillit/temp/investigate/"

    def test_write_within_prefix_allowed(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("AUTOSKILLIT_ALLOWED_WRITE_PREFIX", self.PREFIX)
        event = _build_event("Write", "/clone/.autoskillit/temp/investigate/report.md")
        result = _run_hook(event)
        assert result == ""

    def test_write_outside_prefix_denied(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("AUTOSKILLIT_ALLOWED_WRITE_PREFIX", self.PREFIX)
        event = _build_event("Write", "/clone/src/autoskillit/foo.py")
        result = _run_hook(event)
        parsed = json.loads(result)
        decision = parsed["hookSpecificOutput"]["permissionDecision"]
        reason = parsed["hookSpecificOutput"]["permissionDecisionReason"]
        assert decision == "deny"
        assert "read-only skill session" in reason

    def test_edit_outside_prefix_denied(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("AUTOSKILLIT_ALLOWED_WRITE_PREFIX", self.PREFIX)
        event = _build_event("Edit", "/clone/tests/test_foo.py")
        result = _run_hook(event)
        parsed = json.loads(result)
        assert parsed["hookSpecificOutput"]["permissionDecision"] == "deny"

    def test_malformed_json_denies_in_readonly_session(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("AUTOSKILLIT_ALLOWED_WRITE_PREFIX", self.PREFIX)
        result = _run_hook("not valid json")
        parsed = json.loads(result)
        assert parsed["hookSpecificOutput"]["permissionDecision"] == "deny"
        assert "malformed" in parsed["hookSpecificOutput"]["permissionDecisionReason"]

    def test_missing_file_path_denies(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("AUTOSKILLIT_ALLOWED_WRITE_PREFIX", self.PREFIX)
        event = {"tool_name": "Write", "tool_input": {}}
        result = _run_hook(event)
        parsed = json.loads(result)
        assert parsed["hookSpecificOutput"]["permissionDecision"] == "deny"
        assert "no file_path" in parsed["hookSpecificOutput"]["permissionDecisionReason"]

    def test_non_write_tool_allowed(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("AUTOSKILLIT_ALLOWED_WRITE_PREFIX", self.PREFIX)
        event = {"tool_name": "Read", "tool_input": {"file_path": "/clone/src/foo.py"}}
        result = _run_hook(event)
        assert result == ""

    def test_symlink_resolved(self, monkeypatch: pytest.MonkeyPatch, tmp_path):
        real_dir = tmp_path / "real"
        real_dir.mkdir()
        target_file = real_dir / "secret.py"
        target_file.write_text("x")

        allowed = tmp_path / "allowed"
        allowed.mkdir()
        link = allowed / "link.py"
        link.symlink_to(target_file)

        monkeypatch.setenv("AUTOSKILLIT_ALLOWED_WRITE_PREFIX", str(allowed) + "/")
        event = _build_event("Write", str(link))
        result = _run_hook(event)
        parsed = json.loads(result)
        assert parsed["hookSpecificOutput"]["permissionDecision"] == "deny"
