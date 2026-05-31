"""Tests for background_exec_guard.py PreToolUse hook."""

from __future__ import annotations

import io
import json
import os
from contextlib import redirect_stdout
from unittest.mock import patch


def _run_guard(
    event: dict,
    *,
    headless: bool = False,
    session_type: str | None = None,
    raw_stdin: str | None = None,
) -> str:
    """Run main() with the given PreToolUse event envelope.

    raw_stdin: if provided, passed directly to stdin instead of json.dumps(event).
    Use this to test malformed-input paths.
    """
    from autoskillit.hooks.guards.background_exec_guard import main

    stdin_content = raw_stdin if raw_stdin is not None else json.dumps(event)
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


def _run_guard_headless(event: dict, session_type: str = "skill") -> dict:
    """Run guard in headless mode and parse output JSON."""
    out = _run_guard(event, headless=True, session_type=session_type)
    return json.loads(out) if out.strip() else {}


def test_denies_bash_run_in_background_skill_session():
    response = _run_guard_headless(
        {"tool_name": "Bash", "tool_input": {"command": "echo test", "run_in_background": True}},
        session_type="skill",
    )
    assert response["hookSpecificOutput"]["permissionDecision"] == "deny"


def test_denies_agent_run_in_background_skill_session():
    response = _run_guard_headless(
        {
            "tool_name": "Agent",
            "tool_input": {"prompt": "do something", "run_in_background": True},
        },
        session_type="skill",
    )
    assert response["hookSpecificOutput"]["permissionDecision"] == "deny"


def test_allows_bash_without_run_in_background():
    out = _run_guard(
        {"tool_name": "Bash", "tool_input": {"command": "ls"}},
        headless=True,
        session_type="skill",
    )
    assert not out.strip()


def test_allows_bash_run_in_background_false():
    out = _run_guard(
        {"tool_name": "Bash", "tool_input": {"command": "echo test", "run_in_background": False}},
        headless=True,
        session_type="skill",
    )
    assert not out.strip()


def test_allows_orchestrator_session():
    out = _run_guard(
        {"tool_name": "Bash", "tool_input": {"command": "echo test", "run_in_background": True}},
        headless=True,
        session_type="orchestrator",
    )
    assert not out.strip()


def test_allows_fleet_session():
    out = _run_guard(
        {"tool_name": "Bash", "tool_input": {"command": "echo test", "run_in_background": True}},
        headless=True,
        session_type="fleet",
    )
    assert not out.strip()


def test_allows_interactive_session():
    out = _run_guard(
        {"tool_name": "Bash", "tool_input": {"command": "echo test", "run_in_background": True}},
        headless=False,
    )
    assert not out.strip()


def test_denies_in_subagent_context():
    """Subagents are NOT exempt — run_in_background=true is prohibited regardless."""
    response = _run_guard_headless(
        {
            "tool_name": "Bash",
            "tool_input": {"command": "echo test", "run_in_background": True},
            "agent_id": "sub-123",
        },
        session_type="skill",
    )
    assert response["hookSpecificOutput"]["permissionDecision"] == "deny"


def test_fail_open_malformed_json():
    out = _run_guard({}, headless=True, session_type="skill", raw_stdin="not json")
    assert not out.strip()


def test_fail_open_missing_tool_input():
    out = _run_guard(
        {"tool_name": "Bash"},
        headless=True,
        session_type="skill",
    )
    assert not out.strip()


def test_denies_unset_session_type():
    """Fail-closed: headless with no SESSION_TYPE is treated as skill session → deny."""
    from autoskillit.hooks.guards.background_exec_guard import main

    stdin_content = json.dumps(
        {"tool_name": "Bash", "tool_input": {"command": "echo test", "run_in_background": True}}
    )
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


def test_deny_reason_references_adr():
    response = _run_guard_headless(
        {"tool_name": "Bash", "tool_input": {"command": "echo test", "run_in_background": True}},
        session_type="skill",
    )
    assert response["hookSpecificOutput"]["permissionDecision"] == "deny"
    reason = response["hookSpecificOutput"]["permissionDecisionReason"]
    assert "ADR-0001" in reason
