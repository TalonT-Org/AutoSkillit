"""Tests for the ask_user_question_guard PreToolUse hook."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path


@dataclass
class RunResult:
    exit_code: int
    stdout: str
    stderr: str


def run_guard(
    hook_input: dict, env_extra: dict | None = None, *, headless: bool = False
) -> RunResult:
    hook_path = (
        Path(__file__).resolve().parents[2] / "src/autoskillit/hooks/ask_user_question_guard.py"
    )
    extra = dict(env_extra or {})
    if headless:
        extra["AUTOSKILLIT_HEADLESS"] = "1"
    else:
        extra.pop("AUTOSKILLIT_HEADLESS", None)
    # Start from a clean env without AUTOSKILLIT_HEADLESS to avoid test-env leakage
    env = {k: v for k, v in os.environ.items() if k != "AUTOSKILLIT_HEADLESS"}
    env.update(extra)
    result = subprocess.run(
        [sys.executable, str(hook_path)],
        input=json.dumps(hook_input),
        capture_output=True,
        text=True,
        env=env,
    )
    return RunResult(exit_code=result.returncode, stdout=result.stdout, stderr=result.stderr)


# Existing tests (updated to use headless=True where the headless path matters)


def test_guard_denies_when_no_marker(tmp_path: Path) -> None:
    hook_input = {
        "tool_name": "AskUserQuestion",
        "tool_input": {},
        "session_id": "session-xyz",
        "hook_event_name": "PreToolUse",
    }
    result = run_guard(
        hook_input,
        env_extra={"AUTOSKILLIT_STATE_DIR": str(tmp_path)},
        headless=True,
    )
    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    hook_out = payload["hookSpecificOutput"]
    assert hook_out["hookEventName"] == "PreToolUse"
    assert hook_out["permissionDecision"] == "deny"
    reason = hook_out["permissionDecisionReason"]
    assert "headless sessions" in reason
    assert "open_kitchen first" in reason


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
        headless=True,
    )
    assert result.exit_code == 0
    assert result.stdout.strip() == ""  # permit path exits silently


def test_guard_permits_unrelated_tool(tmp_path: Path) -> None:
    result = run_guard(
        {"tool_name": "Read", "session_id": "x"},
        env_extra={"AUTOSKILLIT_STATE_DIR": str(tmp_path)},
    )
    assert result.exit_code == 0
    assert result.stdout.strip() == ""  # non-AskUserQuestion exits silently


# New tests: session-type axis


def test_guard_permits_interactive_session(tmp_path: Path) -> None:
    """Interactive sessions must always permit AskUserQuestion — user is present."""
    hook_input = {
        "tool_name": "AskUserQuestion",
        "tool_input": {},
        "session_id": "session-interactive",
        "hook_event_name": "PreToolUse",
    }
    # No kitchen marker, no AUTOSKILLIT_HEADLESS — the exact bug scenario
    result = run_guard(
        hook_input,
        env_extra={"AUTOSKILLIT_STATE_DIR": str(tmp_path)},
        headless=False,
    )
    assert result.exit_code == 0
    assert result.stdout.strip() == ""  # must permit silently


def test_guard_denies_headless_without_marker(tmp_path: Path) -> None:
    """Headless sessions without a kitchen marker must be denied."""
    hook_input = {
        "tool_name": "AskUserQuestion",
        "tool_input": {},
        "session_id": "session-headless-no-marker",
        "hook_event_name": "PreToolUse",
    }
    result = run_guard(
        hook_input,
        env_extra={"AUTOSKILLIT_STATE_DIR": str(tmp_path)},
        headless=True,
    )
    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    hook_out = payload["hookSpecificOutput"]
    assert hook_out["permissionDecision"] == "deny"


def test_guard_permits_headless_with_marker(tmp_path: Path) -> None:
    """Headless sessions with a fresh kitchen marker must be permitted."""
    marker_dir = tmp_path / "kitchen_state"
    marker_dir.mkdir(parents=True)
    (marker_dir / "session-headless-ok.json").write_text(
        json.dumps(
            {
                "session_id": "session-headless-ok",
                "opened_at": datetime.now(UTC).isoformat(),
                "recipe_name": "test_recipe",
                "marker_version": 1,
            }
        )
    )
    hook_input = {
        "tool_name": "AskUserQuestion",
        "tool_input": {},
        "session_id": "session-headless-ok",
        "hook_event_name": "PreToolUse",
    }
    result = run_guard(
        hook_input,
        env_extra={"AUTOSKILLIT_STATE_DIR": str(tmp_path)},
        headless=True,
    )
    assert result.exit_code == 0
    assert result.stdout.strip() == ""  # permit path exits silently


def test_guard_denies_stale_marker(tmp_path: Path) -> None:
    """Headless sessions with a stale marker (>24h) must be denied."""
    marker_dir = tmp_path / "kitchen_state"
    marker_dir.mkdir(parents=True)
    stale_time = datetime.now(UTC) - timedelta(hours=25)
    (marker_dir / "session-stale.json").write_text(
        json.dumps(
            {
                "session_id": "session-stale",
                "opened_at": stale_time.isoformat(),
                "recipe_name": "old_recipe",
                "marker_version": 1,
            }
        )
    )
    hook_input = {
        "tool_name": "AskUserQuestion",
        "tool_input": {},
        "session_id": "session-stale",
        "hook_event_name": "PreToolUse",
    }
    result = run_guard(
        hook_input,
        env_extra={"AUTOSKILLIT_STATE_DIR": str(tmp_path)},
        headless=True,
    )
    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    hook_out = payload["hookSpecificOutput"]
    assert hook_out["permissionDecision"] == "deny"
