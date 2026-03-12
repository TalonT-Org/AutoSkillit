"""Tests for headless_orchestration_guard.py PreToolUse hook."""

from __future__ import annotations

import io
import json
import os
from contextlib import redirect_stdout
from unittest.mock import patch

import pytest

_ORCHESTRATION_TOOLS = ["run_skill", "run_cmd", "run_python"]


def _run_guard(tool_input: dict, *, headless: bool = False, raw_stdin: str | None = None) -> str:
    """Run main() with the given event, optionally setting AUTOSKILLIT_HEADLESS=1.

    raw_stdin: if provided, passed directly to stdin instead of json.dumps(tool_input).
    Use this to test malformed-input paths without duplicating setup boilerplate.
    """
    from autoskillit.hooks.headless_orchestration_guard import main

    stdin_content = raw_stdin if raw_stdin is not None else json.dumps(tool_input)
    env_updates = {"AUTOSKILLIT_HEADLESS": "1"} if headless else {}
    with (
        patch.dict(os.environ, env_updates, clear=False),
        patch("sys.stdin", io.StringIO(stdin_content)),
    ):
        buf = io.StringIO()
        with redirect_stdout(buf):
            try:
                main()
            except SystemExit:
                pass
        return buf.getvalue()


def _run_guard_headless(tool_input: dict) -> dict:
    """Run guard in headless mode and parse output JSON."""
    out = _run_guard(tool_input, headless=True)
    return json.loads(out) if out.strip() else {}


@pytest.mark.parametrize("tool_name", _ORCHESTRATION_TOOLS)
def test_guard_denies_orchestration_tools_when_headless(tool_name):
    response = _run_guard_headless({"tool_name": f"mcp__autoskillit__{tool_name}"})
    assert response["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert "headless" in response["hookSpecificOutput"]["permissionDecisionReason"].lower()


def test_guard_allows_non_headless():
    out = _run_guard({"tool_name": "mcp__autoskillit__run_skill"}, headless=False)
    assert not out.strip()


def test_guard_allows_non_orchestration_tool_when_headless():
    response = _run_guard_headless({"tool_name": "mcp__autoskillit__fetch_github_issue"})
    assert response == {}


def test_guard_allows_merge_worktree_when_headless():
    """merge_worktree is not an orchestration tool — workers can still call it."""
    response = _run_guard_headless({"tool_name": "mcp__autoskillit__merge_worktree"})
    assert response == {}


def test_guard_fails_open_on_malformed_input():
    """Malformed stdin must not raise — hook exits 0 silently."""
    out = _run_guard({}, headless=True, raw_stdin="not-json")
    assert not out.strip()
