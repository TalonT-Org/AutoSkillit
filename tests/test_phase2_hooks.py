"""Phase 2 tests: open_kitchen_guard PreToolUse hook."""

from __future__ import annotations

import json
import os
import subprocess
import sys

from autoskillit.core.paths import pkg_root


def _run_guard(env_extra: dict, tool_input: dict) -> dict:
    hook_path = pkg_root() / "hooks" / "open_kitchen_guard.py"
    stdin_payload = json.dumps({"tool_input": tool_input})
    env = {**os.environ, **env_extra}
    result = subprocess.run(
        [sys.executable, str(hook_path)],
        input=stdin_payload,
        capture_output=True,
        text=True,
        env=env,
    )
    return json.loads(result.stdout) if result.stdout.strip() else {}


def test_open_kitchen_guard_denies_headless() -> None:
    response = _run_guard({"AUTOSKILLIT_HEADLESS": "1"}, {})
    assert response["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert "headless" in response["hookSpecificOutput"]["permissionDecisionReason"].lower()


def test_open_kitchen_guard_allows_human_session() -> None:
    env_without_headless = {k: v for k, v in os.environ.items() if k != "AUTOSKILLIT_HEADLESS"}
    hook_path = pkg_root() / "hooks" / "open_kitchen_guard.py"
    stdin_payload = json.dumps({"tool_input": {}})
    result = subprocess.run(
        [sys.executable, str(hook_path)],
        input=stdin_payload,
        capture_output=True,
        text=True,
        env=env_without_headless,
    )
    assert result.returncode == 0, (
        f"Hook exited non-zero: {result.returncode}\nstderr: {result.stderr}"
    )
    assert not result.stdout.strip(), (
        f"Hook must emit no output for non-headless sessions, got: {result.stdout!r}"
    )
