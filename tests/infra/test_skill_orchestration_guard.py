"""Tests for skill_orchestration_guard.py PreToolUse hook."""

from __future__ import annotations

import io
import json
import os
from contextlib import redirect_stdout
from unittest.mock import patch

import pytest

_ORCHESTRATION_TOOLS = ["run_skill", "run_cmd", "run_python"]


def _run_guard(
    tool_input: dict,
    *,
    headless: bool = False,
    session_type: str | None = None,
    raw_stdin: str | None = None,
) -> str:
    """Run main() with the given event, optionally setting headless/session_type env vars.

    raw_stdin: if provided, passed directly to stdin instead of json.dumps(tool_input).
    Use this to test malformed-input paths without duplicating setup boilerplate.
    """
    from autoskillit.hooks.guards.skill_orchestration_guard import main

    stdin_content = raw_stdin if raw_stdin is not None else json.dumps(tool_input)
    env_updates: dict[str, str] = {}
    if headless:
        env_updates["AUTOSKILLIT_HEADLESS"] = "1"
    if session_type is not None:
        env_updates["AUTOSKILLIT_SESSION_TYPE"] = session_type
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


def _run_guard_headless(tool_input: dict, session_type: str = "skill") -> dict:
    """Run guard in headless mode and parse output JSON."""
    out = _run_guard(tool_input, headless=True, session_type=session_type)
    return json.loads(out) if out.strip() else {}


@pytest.mark.parametrize("tool_name", _ORCHESTRATION_TOOLS)
def test_guard_denies_skill_tier(tool_name):
    response = _run_guard_headless(
        {"tool_name": f"mcp__autoskillit__{tool_name}"}, session_type="skill"
    )
    assert response["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert "skill" in response["hookSpecificOutput"]["permissionDecisionReason"].lower()


@pytest.mark.parametrize("tool_name", _ORCHESTRATION_TOOLS)
def test_guard_denies_deprecated_leaf_tier(tool_name):
    """Backward compat: SESSION_TYPE=leaf (old value) is also denied."""
    response = _run_guard_headless(
        {"tool_name": f"mcp__autoskillit__{tool_name}"}, session_type="leaf"
    )
    assert response["hookSpecificOutput"]["permissionDecision"] == "deny"


def test_guard_permits_orchestrator_tier():
    out = _run_guard(
        {"tool_name": "mcp__autoskillit__run_skill"},
        headless=True,
        session_type="orchestrator",
    )
    assert not out.strip()


def test_guard_permits_fleet_tier():
    out = _run_guard(
        {"tool_name": "mcp__autoskillit__run_skill"},
        headless=True,
        session_type="fleet",
    )
    assert not out.strip()


def test_guard_denies_when_session_type_unset():
    """Fail-closed: headless with no SESSION_TYPE is treated as skill session → deny."""
    from autoskillit.hooks.guards.skill_orchestration_guard import main

    stdin_content = json.dumps({"tool_name": "mcp__autoskillit__run_skill"})
    env_without_session_type = {
        k: v for k, v in os.environ.items() if k != "AUTOSKILLIT_SESSION_TYPE"
    }
    env_without_session_type["AUTOSKILLIT_HEADLESS"] = "1"
    with (
        patch.dict(os.environ, env_without_session_type, clear=True),
        patch("sys.stdin", io.StringIO(stdin_content)),
    ):
        buf = io.StringIO()
        with redirect_stdout(buf):
            try:
                main()
            except SystemExit:
                pass
        out = buf.getvalue()
    response = json.loads(out) if out.strip() else {}
    assert response["hookSpecificOutput"]["permissionDecision"] == "deny"


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


@pytest.mark.parametrize("tool_name", _ORCHESTRATION_TOOLS)
def test_skill_orchestration_guard_permits_fleet_session(tool_name):
    response = _run_guard_headless(
        {"tool_name": f"mcp__autoskillit__{tool_name}"}, session_type="fleet"
    )
    assert response == {}, f"Expected fleet to be permitted for {tool_name}"
