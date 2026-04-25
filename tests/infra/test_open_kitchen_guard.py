"""Phase 2 tests: open_kitchen_guard PreToolUse hook."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

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


def test_open_kitchen_guard_denies_leaf_tier() -> None:
    response = _run_guard({"AUTOSKILLIT_HEADLESS": "1", "AUTOSKILLIT_SESSION_TYPE": "leaf"}, {})
    assert response["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert "leaf" in response["hookSpecificOutput"]["permissionDecisionReason"].lower()


def test_open_kitchen_guard_denies_fleet_tier() -> None:
    response = _run_guard(
        {"AUTOSKILLIT_HEADLESS": "1", "AUTOSKILLIT_SESSION_TYPE": "fleet"}, {}
    )
    assert response["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert "fleet" in response["hookSpecificOutput"]["permissionDecisionReason"].lower()


def test_open_kitchen_guard_permits_headless_orchestrator(tmp_path: Path) -> None:
    hook_path = pkg_root() / "hooks" / "open_kitchen_guard.py"
    hook_input = {
        "tool_name": "mcp__autoskillit__open_kitchen",
        "tool_input": {"name": "my_recipe"},
        "session_id": "session-orch",
        "hook_event_name": "PreToolUse",
    }
    env = {
        **os.environ,
        "AUTOSKILLIT_HEADLESS": "1",
        "AUTOSKILLIT_SESSION_TYPE": "orchestrator",
        "AUTOSKILLIT_STATE_DIR": str(tmp_path),
    }
    result = subprocess.run(
        [sys.executable, str(hook_path)],
        input=json.dumps(hook_input),
        capture_output=True,
        text=True,
        env=env,
    )
    assert result.returncode == 0
    if result.stdout.strip():
        payload = json.loads(result.stdout)
        hook_out = payload.get("hookSpecificOutput", {})
        assert hook_out.get("permissionDecision") != "deny"
    marker_path = tmp_path / "kitchen_state" / "session-orch.json"
    assert marker_path.exists(), f"Marker not written at {marker_path}"


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


def test_open_kitchen_guard_writes_marker_on_permit(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("AUTOSKILLIT_STATE_DIR", str(tmp_path))
    monkeypatch.delenv("AUTOSKILLIT_HEADLESS", raising=False)
    hook_input = {
        "tool_name": "mcp__autoskillit__open_kitchen",
        "tool_input": {"name": "my_recipe"},
        "session_id": "session-abc",
        "hook_event_name": "PreToolUse",
    }
    hook_path = pkg_root() / "hooks" / "open_kitchen_guard.py"
    env = {k: v for k, v in os.environ.items() if k != "AUTOSKILLIT_HEADLESS"}
    env["AUTOSKILLIT_STATE_DIR"] = str(tmp_path)
    result = subprocess.run(
        [sys.executable, str(hook_path)],
        input=json.dumps(hook_input),
        capture_output=True,
        text=True,
        env=env,
    )
    assert result.returncode == 0
    if result.stdout.strip():
        payload = json.loads(result.stdout)
        hook_out = payload.get("hookSpecificOutput", {})
        assert hook_out.get("permissionDecision") != "deny"
    marker_path = tmp_path / "kitchen_state" / "session-abc.json"
    assert marker_path.exists(), f"Marker not written at {marker_path}"
    data = json.loads(marker_path.read_text())
    assert data["session_id"] == "session-abc"
    assert data["recipe_name"] == "my_recipe"
    assert data["marker_version"] == 1


def test_open_kitchen_guard_no_marker_on_deny(tmp_path: Path, monkeypatch) -> None:
    """When headless, the guard denies; no marker should be written."""
    monkeypatch.setenv("AUTOSKILLIT_STATE_DIR", str(tmp_path))
    hook_path = pkg_root() / "hooks" / "open_kitchen_guard.py"
    env = {
        **os.environ,
        "AUTOSKILLIT_HEADLESS": "1",
        "AUTOSKILLIT_SESSION_TYPE": "leaf",
        "AUTOSKILLIT_STATE_DIR": str(tmp_path),
    }
    result = subprocess.run(
        [sys.executable, str(hook_path)],
        input=json.dumps(
            {
                "tool_name": "mcp__autoskillit__open_kitchen",
                "tool_input": {"name": "my_recipe"},
                "session_id": "session-abc",
                "hook_event_name": "PreToolUse",
            }
        ),
        capture_output=True,
        text=True,
        env=env,
    )
    assert result.returncode == 0
    payload = json.loads(result.stdout)
    assert payload["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert not (tmp_path / "kitchen_state" / "session-abc.json").exists()


# --- Group P-3: Hook namespacing ---


def test_open_kitchen_guard_uses_campaign_namespace(tmp_path: Path, monkeypatch) -> None:
    """open_kitchen_guard writes marker to campaign-namespaced directory."""
    monkeypatch.delenv("AUTOSKILLIT_STATE_DIR", raising=False)
    monkeypatch.setenv("AUTOSKILLIT_CAMPAIGN_ID", "camp-77")
    monkeypatch.chdir(tmp_path)
    from autoskillit.hooks.open_kitchen_guard import _write_kitchen_marker

    _write_kitchen_marker("sess-test", "my-recipe")
    expected = tmp_path / ".autoskillit" / "temp" / "kitchen_state" / "camp-77" / "sess-test.json"
    assert expected.exists()


def test_open_kitchen_guard_denies_fleet_headless() -> None:
    response = _run_guard({"AUTOSKILLIT_HEADLESS": "1", "AUTOSKILLIT_SESSION_TYPE": "fleet"}, {})
    assert response["hookSpecificOutput"]["permissionDecision"] == "deny"


def test_open_kitchen_guard_fleet_denial_has_specific_message() -> None:
    """Fleet denial message must mention fleet or franchise, not the generic leaf message."""
    response = _run_guard({"AUTOSKILLIT_HEADLESS": "1", "AUTOSKILLIT_SESSION_TYPE": "fleet"}, {})
    reason = response["hookSpecificOutput"]["permissionDecisionReason"].lower()
    assert "fleet" in reason or "franchise" in reason
