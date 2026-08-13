"""Phase 2 tests: open_kitchen_guard PreToolUse hook."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from autoskillit.core.paths import pkg_root
from tests._helpers import seed_registry_owner

pytestmark = [pytest.mark.layer("infra"), pytest.mark.medium]


def _run_guard(env_extra: dict, tool_input: dict) -> dict:
    hook_path = pkg_root() / "hooks" / "guards" / "open_kitchen_guard.py"
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


def test_open_kitchen_guard_denies_skill_tier() -> None:
    response = _run_guard({"AUTOSKILLIT_HEADLESS": "1", "AUTOSKILLIT_SESSION_TYPE": "skill"}, {})
    assert response["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert "skill" in response["hookSpecificOutput"]["permissionDecisionReason"].lower()


def test_open_kitchen_guard_denies_fleet_tier() -> None:
    response = _run_guard({"AUTOSKILLIT_HEADLESS": "1", "AUTOSKILLIT_SESSION_TYPE": "fleet"}, {})
    assert response["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert "fleet" in response["hookSpecificOutput"]["permissionDecisionReason"].lower()


def test_open_kitchen_guard_permits_headless_orchestrator(tmp_path: Path) -> None:
    hook_path = pkg_root() / "hooks" / "guards" / "open_kitchen_guard.py"
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
    hook_path = pkg_root() / "hooks" / "guards" / "open_kitchen_guard.py"
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
    hook_path = pkg_root() / "hooks" / "guards" / "open_kitchen_guard.py"
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
    hook_path = pkg_root() / "hooks" / "guards" / "open_kitchen_guard.py"
    env = {
        **os.environ,
        "AUTOSKILLIT_HEADLESS": "1",
        "AUTOSKILLIT_SESSION_TYPE": "skill",
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
    from autoskillit.hooks.guards.open_kitchen_guard import _write_kitchen_marker

    _write_kitchen_marker("sess-test", "my-recipe")
    expected = tmp_path / ".autoskillit" / "temp" / "kitchen_state" / "camp-77" / "sess-test.json"
    assert expected.exists()


def test_open_kitchen_guard_denies_fleet_headless() -> None:
    response = _run_guard({"AUTOSKILLIT_HEADLESS": "1", "AUTOSKILLIT_SESSION_TYPE": "fleet"}, {})
    assert response["hookSpecificOutput"]["permissionDecision"] == "deny"


def test_open_kitchen_guard_fleet_denial_has_specific_message() -> None:
    """Fleet denial message must mention fleet or franchise, not the generic skill message."""
    response = _run_guard({"AUTOSKILLIT_HEADLESS": "1", "AUTOSKILLIT_SESSION_TYPE": "fleet"}, {})
    reason = response["hookSpecificOutput"]["permissionDecisionReason"].lower()
    assert "fleet" in reason or "franchise" in reason


def test_guard_bridges_launch_id_to_registry(tmp_path: Path) -> None:
    """open_kitchen_guard bridges AUTOSKILLIT_LAUNCH_ID to claude_session_id in registry."""
    from autoskillit.core.runtime.session_registry import (
        read_registry,
        write_registry_entry,
    )

    project_dir = tmp_path
    write_registry_entry(project_dir, "abc", "cook", None)
    seed_registry_owner(project_dir, "abc")
    seeded_entry = read_registry(project_dir)["abc"]
    assert seeded_entry["claude_session_id"] is None
    seeded_owner = {key: value for key, value in seeded_entry.items() if key.startswith("owner_")}

    hook_path = pkg_root() / "hooks" / "guards" / "open_kitchen_guard.py"
    hook_input = {
        "tool_name": "mcp__autoskillit__open_kitchen",
        "tool_input": {},
        "session_id": "claude-xyz",
        "hook_event_name": "PreToolUse",
    }
    env_without_headless = {k: v for k, v in os.environ.items() if k != "AUTOSKILLIT_HEADLESS"}
    env_without_headless["AUTOSKILLIT_LAUNCH_ID"] = "abc"
    result = subprocess.run(
        [sys.executable, str(hook_path)],
        input=json.dumps(hook_input),
        capture_output=True,
        text=True,
        env=env_without_headless,
        cwd=str(project_dir),
    )
    assert result.returncode == 0, f"Hook failed: {result.stderr}"
    registry = read_registry(project_dir)
    assert registry["abc"]["claude_session_id"] == "claude-xyz"
    assert {
        key: value for key, value in registry["abc"].items() if key.startswith("owner_")
    } == seeded_owner


def test_guard_bridge_no_op_when_no_launch_id(tmp_path: Path) -> None:
    """open_kitchen_guard bridge is a no-op when AUTOSKILLIT_LAUNCH_ID is not set."""
    from autoskillit.core.runtime.session_registry import read_registry, write_registry_entry

    project_dir = tmp_path
    write_registry_entry(project_dir, "abc", "cook", None)

    hook_path = pkg_root() / "hooks" / "guards" / "open_kitchen_guard.py"
    hook_input = {
        "tool_name": "mcp__autoskillit__open_kitchen",
        "tool_input": {},
        "session_id": "claude-xyz",
        "hook_event_name": "PreToolUse",
    }
    env_without = {
        k: v
        for k, v in os.environ.items()
        if k not in ("AUTOSKILLIT_HEADLESS", "AUTOSKILLIT_LAUNCH_ID")
    }
    result = subprocess.run(
        [sys.executable, str(hook_path)],
        input=json.dumps(hook_input),
        capture_output=True,
        text=True,
        env=env_without,
        cwd=str(project_dir),
    )
    assert result.returncode == 0, f"Hook failed: {result.stderr}"
    registry = read_registry(project_dir)
    assert registry["abc"]["claude_session_id"] is None


# --- Recipe Reload Guard: T2 tests ---


def _write_confirmed_marker(tmp_path: Path, session_id: str) -> None:
    state_dir = tmp_path / "kitchen_state"
    state_dir.mkdir(parents=True, exist_ok=True)
    (state_dir / f"{session_id}_recipe_confirmed.json").write_text(
        json.dumps({"session_id": session_id, "confirmed_at": "2026-06-08T00:00:00+00:00"})
    )


def _run_guard_with_session(
    tmp_path: Path,
    session_id: str,
    tool_input: dict,
    env_extra: dict | None = None,
) -> dict:
    hook_path = pkg_root() / "hooks" / "guards" / "open_kitchen_guard.py"
    hook_input = {
        "tool_name": "mcp__autoskillit__open_kitchen",
        "tool_input": tool_input,
        "session_id": session_id,
        "hook_event_name": "PreToolUse",
    }
    env = {
        k: v
        for k, v in os.environ.items()
        if k not in ("AUTOSKILLIT_HEADLESS", "AUTOSKILLIT_STATE_DIR")
    }
    env["AUTOSKILLIT_STATE_DIR"] = str(tmp_path)
    if env_extra:
        env.update(env_extra)
    result = subprocess.run(
        [sys.executable, str(hook_path)],
        input=json.dumps(hook_input),
        capture_output=True,
        text=True,
        env=env,
    )
    return {"returncode": result.returncode, "stdout": result.stdout, "stderr": result.stderr}


# T2-1: Reload blocked after confirmed marker
def test_reload_blocked_after_confirmed_marker(tmp_path: Path) -> None:
    _write_confirmed_marker(tmp_path, "sess-abc")
    result = _run_guard_with_session(tmp_path, "sess-abc", {"name": "implementation"})
    assert result["returncode"] == 0
    payload = json.loads(result["stdout"])
    assert payload["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert "RECIPE ALREADY LOADED" in payload["hookSpecificOutput"]["permissionDecisionReason"]


# T2-2: Gate-only open allowed after confirmed
def test_gate_only_open_allowed_after_confirmed(tmp_path: Path) -> None:
    _write_confirmed_marker(tmp_path, "sess-abc")
    result = _run_guard_with_session(tmp_path, "sess-abc", {})
    assert result["returncode"] == 0
    # Either no stdout, or stdout with permissionDecision != "deny"
    if result["stdout"].strip():
        payload = json.loads(result["stdout"])
        assert payload.get("hookSpecificOutput", {}).get("permissionDecision") != "deny"


# T2-3: ingredients_only=True allowed after confirmed
def test_ingredients_only_allowed_after_confirmed(tmp_path: Path) -> None:
    _write_confirmed_marker(tmp_path, "sess-abc")
    result = _run_guard_with_session(
        tmp_path, "sess-abc", {"name": "impl", "ingredients_only": True}
    )
    assert result["returncode"] == 0
    if result["stdout"].strip():
        payload = json.loads(result["stdout"])
        assert payload.get("hookSpecificOutput", {}).get("permissionDecision") != "deny"


# T2-4: Different session_id not blocked
def test_different_session_id_not_blocked(tmp_path: Path) -> None:
    _write_confirmed_marker(tmp_path, "sess-other")
    result = _run_guard_with_session(tmp_path, "sess-abc", {"name": "implementation"})
    assert result["returncode"] == 0
    if result["stdout"].strip():
        payload = json.loads(result["stdout"])
        assert payload.get("hookSpecificOutput", {}).get("permissionDecision") != "deny"


# T2-5: No confirmed marker allows load (initialization phase)
def test_no_confirmed_marker_allows_load(tmp_path: Path) -> None:
    # Do NOT create any confirmed marker
    result = _run_guard_with_session(tmp_path, "sess-abc", {"name": "implementation"})
    assert result["returncode"] == 0
    if result["stdout"].strip():
        payload = json.loads(result["stdout"])
        assert payload.get("hookSpecificOutput", {}).get("permissionDecision") != "deny"


# T2-6: Corrupted confirmed marker fails open
def test_corrupted_confirmed_marker_fails_open(tmp_path: Path) -> None:
    state_dir = tmp_path / "kitchen_state"
    state_dir.mkdir(parents=True, exist_ok=True)
    (state_dir / "sess-abc_recipe_confirmed.json").write_text("not-valid-json")
    result = _run_guard_with_session(tmp_path, "sess-abc", {"name": "implementation"})
    assert result["returncode"] == 0
    if result["stdout"].strip():
        payload = json.loads(result["stdout"])
        assert payload.get("hookSpecificOutput", {}).get("permissionDecision") != "deny"


# T2-7: Reload denied in headless orchestrator after confirmed
def test_reload_denied_in_headless_orchestrator_after_confirmed(tmp_path: Path) -> None:
    _write_confirmed_marker(tmp_path, "sess-abc")
    result = _run_guard_with_session(
        tmp_path,
        "sess-abc",
        {"name": "implementation"},
        env_extra={
            "AUTOSKILLIT_HEADLESS": "1",
            "AUTOSKILLIT_SESSION_TYPE": "orchestrator",
        },
    )
    assert result["returncode"] == 0
    payload = json.loads(result["stdout"])
    assert payload["hookSpecificOutput"]["permissionDecision"] == "deny"
