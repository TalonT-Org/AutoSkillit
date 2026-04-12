"""Tests for the ask_user_question_guard PreToolUse hook."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path


@dataclass
class RunResult:
    exit_code: int
    stdout: str
    stderr: str


def run_guard(hook_input: dict, env_extra: dict | None = None) -> RunResult:
    hook_path = (
        Path(__file__).resolve().parents[2] / "src/autoskillit/hooks/ask_user_question_guard.py"
    )
    env = {**os.environ, **(env_extra or {})}
    result = subprocess.run(
        [sys.executable, str(hook_path)],
        input=json.dumps(hook_input),
        capture_output=True,
        text=True,
        env=env,
    )
    return RunResult(exit_code=result.returncode, stdout=result.stdout, stderr=result.stderr)


def test_guard_denies_when_no_marker(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("AUTOSKILLIT_STATE_DIR", str(tmp_path))
    hook_input = {
        "tool_name": "AskUserQuestion",
        "tool_input": {},
        "session_id": "session-xyz",
        "hook_event_name": "PreToolUse",
    }
    result = run_guard(hook_input, env_extra={"AUTOSKILLIT_STATE_DIR": str(tmp_path)})
    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    hook_out = payload["hookSpecificOutput"]
    assert hook_out["hookEventName"] == "PreToolUse"
    assert hook_out["permissionDecision"] == "deny"
    reason = hook_out["permissionDecisionReason"]
    assert "open_kitchen" in reason
    assert "ToolSearch" in reason


def test_guard_permits_when_marker_present(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("AUTOSKILLIT_STATE_DIR", str(tmp_path))
    marker_dir = tmp_path / "kitchen_state"
    marker_dir.mkdir(parents=True)
    (marker_dir / "session-xyz.json").write_text(
        json.dumps(
            {
                "session_id": "session-xyz",
                "opened_at": datetime.now(UTC).isoformat(),
                "recipe_name": "my_recipe",
                "marker_version": 1,
            }
        )
    )
    result = run_guard(
        {
            "tool_name": "AskUserQuestion",
            "session_id": "session-xyz",
            "hook_event_name": "PreToolUse",
        },
        env_extra={"AUTOSKILLIT_STATE_DIR": str(tmp_path)},
    )
    assert result.exit_code == 0
    if result.stdout.strip():
        payload = json.loads(result.stdout)
        hook_out = payload.get("hookSpecificOutput", {})
        assert hook_out.get("permissionDecision") != "deny"


def test_guard_permits_unrelated_tool(tmp_path: Path) -> None:
    result = run_guard(
        {"tool_name": "Read", "session_id": "x"},
        env_extra={"AUTOSKILLIT_STATE_DIR": str(tmp_path)},
    )
    assert result.exit_code == 0
    if result.stdout.strip():
        payload = json.loads(result.stdout)
        hook_out = payload.get("hookSpecificOutput", {})
        assert hook_out.get("permissionDecision") != "deny"
