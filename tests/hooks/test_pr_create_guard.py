"""Tests for pr_create_guard interpreter bypass detection."""

from __future__ import annotations

import io
import json
from contextlib import redirect_stdout

import pytest

pytestmark = [pytest.mark.layer("infra"), pytest.mark.small]


def _build_bash_event(command: str) -> dict:
    return {"tool_name": "Bash", "tool_input": {"command": command}}


def _run_hook(event: dict, monkeypatch) -> str:
    from autoskillit.hooks.guards.pr_create_guard import main

    stdin_text = json.dumps(event)
    monkeypatch.setattr("sys.stdin", io.StringIO(stdin_text))
    buf = io.StringIO()
    try:
        with redirect_stdout(buf):
            main()
    except SystemExit:
        pass
    return buf.getvalue()


def _set_kitchen_open(tmp_path, monkeypatch):
    cfg = tmp_path / ".autoskillit" / "temp" / ".hook_config.json"
    cfg.parent.mkdir(parents=True)
    cfg.write_text("{}")
    monkeypatch.chdir(tmp_path)


def test_python3_subprocess_gh_pr_create_denied(monkeypatch, tmp_path):
    """python3 wrapping gh pr create via os.system must be caught."""
    _set_kitchen_open(tmp_path, monkeypatch)
    monkeypatch.delenv("AUTOSKILLIT_SKILL_NAME", raising=False)
    monkeypatch.delenv("AUTOSKILLIT_SESSION_TYPE", raising=False)

    cmd = "python3 -c \"import os; os.system('gh pr create --fill')\""
    event = _build_bash_event(cmd)
    output = _run_hook(event, monkeypatch)
    assert output, "Guard produced no output — bypass was not caught"
    parsed = json.loads(output)
    decision = parsed["hookSpecificOutput"]["permissionDecision"]
    assert decision == "deny", f"Expected deny but got: {decision}"


def test_direct_gh_pr_create_still_denied(monkeypatch, tmp_path):
    """Direct gh pr create must still be denied (regression guard)."""
    _set_kitchen_open(tmp_path, monkeypatch)
    monkeypatch.delenv("AUTOSKILLIT_SKILL_NAME", raising=False)
    monkeypatch.delenv("AUTOSKILLIT_SESSION_TYPE", raising=False)

    event = _build_bash_event("gh pr create --fill")
    output = _run_hook(event, monkeypatch)
    assert output
    parsed = json.loads(output)
    assert parsed["hookSpecificOutput"]["permissionDecision"] == "deny"


def test_echo_mentioning_gh_pr_create_allowed(monkeypatch, tmp_path):
    """echo containing 'gh pr create' must not be blocked."""
    _set_kitchen_open(tmp_path, monkeypatch)
    monkeypatch.delenv("AUTOSKILLIT_SKILL_NAME", raising=False)
    monkeypatch.delenv("AUTOSKILLIT_SESSION_TYPE", raising=False)

    event = _build_bash_event("echo 'running gh pr create for docs'")
    output = _run_hook(event, monkeypatch)
    assert output == "", f"Expected allow but guard produced: {output!r}"
