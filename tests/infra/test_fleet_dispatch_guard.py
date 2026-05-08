"""Tests for fleet_dispatch_guard.py PreToolUse hook."""

from __future__ import annotations

import io
import json
import os
from contextlib import redirect_stdout
from unittest.mock import patch

import pytest


def _run_guard(
    tool_input: dict,
    *,
    headless: bool = False,
    session_type: str | None = None,
    raw_stdin: str | None = None,
) -> str:
    """Run main() with the given event payload."""
    from autoskillit.hooks.guards.fleet_dispatch_guard import main

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
            except SystemExit as exc:
                if exc.code not in (None, 0):
                    raise
        return buf.getvalue()


# --- M1: Interactive callers always permitted ---


def test_guard_permits_interactive_caller():
    out = _run_guard(
        {"tool_name": "mcp__plugin_autoskillit_autoskillit__dispatch_food_truck"},
        headless=False,
    )
    assert not out.strip()


# --- M2–M5: Headless callers always denied ---


@pytest.mark.parametrize(
    "session_type",
    ["orchestrator", "fleet", "skill", None],
    ids=["orchestrator", "fleet", "skill", "unset"],
)
def test_guard_denies_headless_regardless_of_session_type(session_type):
    out = _run_guard(
        {"tool_name": "mcp__plugin_autoskillit_autoskillit__dispatch_food_truck"},
        headless=True,
        session_type=session_type,
    )
    response = json.loads(out)
    assert response["hookSpecificOutput"]["permissionDecision"] == "deny"
    reason = response["hookSpecificOutput"]["permissionDecisionReason"].lower()
    assert "headless" in reason or "fleet session" in reason


# --- M7: Unrelated tool passes through ---


def test_guard_ignores_unrelated_tool():
    out = _run_guard(
        {"tool_name": "Bash"},
        headless=True,
        session_type="fleet",
    )
    assert not out.strip()


# --- M8: Malformed input ---


def test_guard_fails_open_on_malformed_input():
    out = _run_guard({}, headless=True, raw_stdin="not-json")
    assert not out.strip()
