"""Phase 2 tests: open_kitchen_guard PreToolUse hook."""

from __future__ import annotations

import fcntl
import inspect
import io
import json
import re
import subprocess
import sys
import time
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from autoskillit.core.paths import pkg_root
from tests._helpers import seed_registry_owner
from tests.conftest import production_interpreter_env

pytestmark = [pytest.mark.layer("infra"), pytest.mark.medium]


def _run_standalone_hook(
    hook_main: Callable[[], None],
    monkeypatch: pytest.MonkeyPatch,
    *,
    payload: dict,
    cwd: Path,
) -> None:
    """Run a stdlib hook in-process with hook-shaped stdin and a foreign cwd."""
    monkeypatch.chdir(cwd)
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(payload)))
    with pytest.raises(SystemExit) as exit_info:
        hook_main()
    assert exit_info.value.code == 0


def test_recipe_confirmation_marker_round_trips_through_state_root(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A recipe-confirmed marker is read by the guard outside the hook cwd."""
    from autoskillit.hooks import recipe_confirmed_post_hook
    from autoskillit.hooks.guards import open_kitchen_guard

    project_dir = tmp_path / "project"
    foreign_cwd = tmp_path / "foreign"
    project_dir.mkdir()
    foreign_cwd.mkdir()
    monkeypatch.setenv("AUTOSKILLIT_STATE_ROOT", str(project_dir))

    _run_standalone_hook(
        recipe_confirmed_post_hook.main,
        monkeypatch,
        cwd=foreign_cwd,
        payload={
            "cwd": str(project_dir),
            "session_id": "session-confirmed",
            "tool_response": json.dumps({"result": json.dumps({"success": True})}),
        },
    )

    marker_path = (
        project_dir
        / ".autoskillit"
        / "temp"
        / "kitchen_state"
        / "session-confirmed_recipe_confirmed.json"
    )
    assert json.loads(marker_path.read_text(encoding="utf-8"))["session_id"] == "session-confirmed"

    _run_standalone_hook(
        open_kitchen_guard.main,
        monkeypatch,
        cwd=foreign_cwd,
        payload={
            "cwd": str(project_dir),
            "tool_name": "mcp__autoskillit__open_kitchen",
            "tool_input": {"name": "implementation"},
            "session_id": "session-confirmed",
            "hook_event_name": "PreToolUse",
        },
    )

    response = json.loads(capsys.readouterr().out)
    assert response["hookSpecificOutput"]["permissionDecision"] == "deny"


def test_session_start_sweeps_kitchen_marker_from_state_root(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The session-start marker sweep uses its supplied state root, not process cwd."""
    from autoskillit.hooks import session_start_hook

    project_dir = tmp_path / "project"
    foreign_cwd = tmp_path / "foreign"
    project_dir.mkdir()
    foreign_cwd.mkdir()
    marker_path = project_dir / ".autoskillit" / "temp" / "kitchen_state" / "stale.json"
    marker_path.parent.mkdir(parents=True)
    marker_path.write_text(
        json.dumps(
            {
                "session_id": "stale",
                "opened_at": (datetime.now(UTC) - timedelta(hours=25)).isoformat(),
                "recipe_name": "implementation",
            }
        ),
        encoding="utf-8",
    )
    transcript = tmp_path / "resumed.jsonl"
    transcript.write_text("resumed", encoding="utf-8")
    monkeypatch.setenv("AUTOSKILLIT_STATE_ROOT", str(project_dir))

    _run_standalone_hook(
        session_start_hook.main,
        monkeypatch,
        cwd=foreign_cwd,
        payload={"cwd": str(project_dir), "transcript_path": str(transcript)},
    )

    assert not marker_path.exists()


def test_hook_config_is_read_from_state_root_outside_hook_cwd(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Quota settings honor the state-root config when a hook runs elsewhere."""
    from autoskillit.hooks import _hook_settings

    project_dir = tmp_path / "project"
    foreign_cwd = tmp_path / "foreign"
    project_dir.mkdir()
    foreign_cwd.mkdir()
    hook_config = project_dir / ".autoskillit" / "temp" / ".hook_config.json"
    hook_config.parent.mkdir(parents=True)
    hook_config.write_text(
        json.dumps(
            {
                "quota_guard": {
                    "cache_path": "/state-root/quota-cache.json",
                    "cache_max_age": 123,
                    "buffer_seconds": 45,
                    "disabled": True,
                }
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("AUTOSKILLIT_STATE_ROOT", str(project_dir))
    monkeypatch.chdir(foreign_cwd)

    settings = _hook_settings.resolve_quota_settings()

    assert settings.cache_path == "/state-root/quota-cache.json"
    assert settings.cache_max_age == 123
    assert settings.buffer_seconds == 45
    assert settings.disabled is True


def test_kitchen_marker_hash_fields_share_one_inert_tracked_annotation() -> None:
    """Placeholder hashes remain one deliberately tracked compatibility surface."""
    from autoskillit.hooks.guards.open_kitchen_guard import _write_kitchen_marker

    lines = inspect.getsource(_write_kitchen_marker).splitlines()
    content_hash_line = next(
        index for index, line in enumerate(lines) if '"content_hash":' in line
    )
    composite_hash_line = next(
        index for index, line in enumerate(lines) if '"composite_hash":' in line
    )
    annotations = [
        (index, match)
        for index, line in enumerate(lines)
        if (match := re.search(r"inert-tracked:#([1-9][0-9]*)", line))
    ]

    assert len(annotations) == 1
    annotation_line, _ = annotations[0]
    assert annotation_line + 1 == content_hash_line
    assert composite_hash_line == content_hash_line + 1
    assert "content_hash" in lines[annotation_line]
    assert "composite_hash" in lines[annotation_line]
    assert "inert-tracked:" not in lines[content_hash_line]
    assert "inert-tracked:" not in lines[composite_hash_line]


def _run_guard(env_extra: dict, tool_input: dict) -> dict:
    hook_path = pkg_root() / "hooks" / "guards" / "open_kitchen_guard.py"
    stdin_payload = json.dumps({"tool_input": tool_input})
    env = {**production_interpreter_env(), **env_extra}
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
        **production_interpreter_env(),
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
    env_without_headless = {
        k: v for k, v in production_interpreter_env().items() if k != "AUTOSKILLIT_HEADLESS"
    }
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
    env = {k: v for k, v in production_interpreter_env().items() if k != "AUTOSKILLIT_HEADLESS"}
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
        **production_interpreter_env(),
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


def test_registry_bridge_lock_contention_stops_at_its_fake_deadline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The permit-path registry bridge never waits indefinitely for its flock."""
    from autoskillit.hooks.guards import open_kitchen_guard as guard_module  # noqa: PLC0415

    timestamps = iter((0.0, 1.0, 2.0))
    sleeps: list[float] = []
    attempts = 0

    def always_contended(_fd: int, _operation: int) -> None:
        nonlocal attempts
        attempts += 1
        raise BlockingIOError("session registry lock is held")

    monkeypatch.setattr(guard_module.time, "monotonic", lambda: next(timestamps))
    monkeypatch.setattr(guard_module.time, "sleep", sleeps.append)
    monkeypatch.setattr(fcntl, "flock", always_contended)

    with pytest.raises(BlockingIOError):
        guard_module._acquire_registry_lock(17)

    assert attempts == 2
    assert sleeps == [guard_module._LOCK_RETRY_INTERVAL_SECONDS]


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
    env_without_headless = {
        k: v for k, v in production_interpreter_env().items() if k != "AUTOSKILLIT_HEADLESS"
    }
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


def test_guard_bridge_rereads_registry_under_lock(tmp_path: Path) -> None:
    from autoskillit.core.runtime.session_registry import (
        read_registry,
        registry_path,
        write_registry_entry,
    )

    project_dir = tmp_path / "project"
    foreign_cwd = tmp_path / "foreign"
    foreign_cwd.mkdir()
    write_registry_entry(project_dir, "launch-a", "cook", None)
    write_registry_entry(project_dir, "launch-b", "cook", "original")
    seed_registry_owner(project_dir, "launch-a")
    seed_registry_owner(project_dir, "launch-b")
    registry_file = registry_path(project_dir)
    lock_file = registry_file.with_suffix(".lock").open("w")
    process: subprocess.Popen[str] | None = None

    hook_input = {
        "tool_name": "mcp__autoskillit__open_kitchen",
        "tool_input": {},
        "session_id": "claude-a",
        "hook_event_name": "PreToolUse",
    }
    env = {
        key: value
        for key, value in production_interpreter_env().items()
        if key not in ("AUTOSKILLIT_HEADLESS", "AUTOSKILLIT_STATE_DIR")
    }
    env.update(
        AUTOSKILLIT_LAUNCH_ID="launch-a",
        AUTOSKILLIT_STATE_ROOT=str(project_dir),
    )

    try:
        fcntl.flock(lock_file, fcntl.LOCK_EX)
        process = subprocess.Popen(
            [sys.executable, str(pkg_root() / "hooks" / "guards" / "open_kitchen_guard.py")],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=env,
            cwd=foreign_cwd,
        )
        assert process.stdin is not None
        process.stdin.write(json.dumps(hook_input))
        process.stdin.close()
        process.stdin = None

        marker = project_dir / ".autoskillit" / "temp" / "kitchen_state" / "claude-a.json"
        deadline = time.monotonic() + 5
        while not marker.exists() and process.poll() is None and time.monotonic() < deadline:
            time.sleep(0.01)
        assert marker.exists()
        assert process.poll() is None

        registry = read_registry(project_dir)
        registry["launch-b"]["recipe_name"] = "preserved-while-locked"
        registry_file.write_text(json.dumps(registry), encoding="utf-8")

        fcntl.flock(lock_file, fcntl.LOCK_UN)
        stdout, stderr = process.communicate(timeout=5)
        assert process.returncode == 0, stderr
        assert stdout == ""

        updated = read_registry(project_dir)
        assert updated["launch-a"]["claude_session_id"] == "claude-a"
        assert updated["launch-b"] == registry["launch-b"]
    finally:
        fcntl.flock(lock_file, fcntl.LOCK_UN)
        lock_file.close()
        if process is not None and process.poll() is None:
            process.terminate()
            process.wait(timeout=5)


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
        for k, v in production_interpreter_env().items()
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
        for k, v in production_interpreter_env().items()
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
