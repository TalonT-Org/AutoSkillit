"""Tests for planner_gh_discovery_guard interpreter bypass detection."""

from __future__ import annotations

import io
import json
from contextlib import redirect_stdout

import pytest

pytestmark = [pytest.mark.layer("infra"), pytest.mark.small]


def _build_bash_event(command: str) -> dict:
    return {"tool_name": "Bash", "tool_input": {"command": command}}


def _run_hook(event: dict, monkeypatch, *, skill_name: str = "planner-test") -> str:
    from autoskillit.hooks.guards.planner_gh_discovery_guard import main

    stdin_text = json.dumps(event)
    monkeypatch.setattr("sys.stdin", io.StringIO(stdin_text))
    monkeypatch.setenv("AUTOSKILLIT_HEADLESS", "1")
    monkeypatch.setenv("AUTOSKILLIT_SKILL_NAME", skill_name)
    buf = io.StringIO()
    try:
        with redirect_stdout(buf):
            main()
    except SystemExit:
        pass
    return buf.getvalue()


def test_python3_os_system_gh_issue_list_denied(monkeypatch):
    """python3 wrapping gh issue list via os.system must be caught."""
    cmd = "python3 -c \"import os; os.system('gh issue list')\""
    output = _run_hook(_build_bash_event(cmd), monkeypatch)
    assert output, "Guard produced no output — bypass was not caught"
    parsed = json.loads(output)
    decision = parsed["hookSpecificOutput"]["permissionDecision"]
    assert decision == "deny", f"Expected deny but got: {decision}"


def test_direct_gh_issue_list_still_denied(monkeypatch):
    """Direct gh issue list must still be denied (regression guard)."""
    output = _run_hook(_build_bash_event("gh issue list"), monkeypatch)
    assert output
    parsed = json.loads(output)
    assert parsed["hookSpecificOutput"]["permissionDecision"] == "deny"


def test_gh_issue_view_allowed(monkeypatch):
    """Targeted gh issue view must not be blocked."""
    output = _run_hook(_build_bash_event("gh issue view 42"), monkeypatch)
    assert output == "", f"Expected allow but guard produced: {output!r}"


def test_non_planner_skill_not_affected(monkeypatch):
    """Guard only fires for planner- skills."""
    output = _run_hook(
        _build_bash_event("gh issue list"),
        monkeypatch,
        skill_name="implement-worktree-no-merge",
    )
    assert output == "", "Non-planner skill should not be blocked"
