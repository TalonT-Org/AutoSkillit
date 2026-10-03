"""Tests for mcp_health_advisor PreToolUse hook."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from autoskillit.core import is_pid_zombie
from tests._hook_protocol_oracle import STATUS_COMPLETED, claude_verdict, codex_verdict, run_hook

pytestmark = [pytest.mark.layer("infra"), pytest.mark.medium]


def _run_guard(
    tmp_path: Path,
    env_extra: dict,
    tool_name: str = "Read",
    kitchens: list[dict] | None = None,
    cwd: Path | str | None = None,
    headless: bool = False,
):
    """Run mcp_health_advisor.py with an isolated home and hook environment."""
    home = tmp_path / "fakehome"
    home.mkdir(exist_ok=True)
    ak_dir = home / ".autoskillit"
    ak_dir.mkdir(exist_ok=True)
    if kitchens is not None:
        (ak_dir / "active_kitchens.json").write_text(
            json.dumps({"kitchens": kitchens, "schema_version": 1})
        )
    env = {"HOME": str(home), "AUTOSKILLIT_LOG_DIR": str(tmp_path / "logs")}
    if headless:
        env["AUTOSKILLIT_HEADLESS"] = "1"
    env.update(env_extra)
    return run_hook(
        "guards/mcp_health_advisor.py",
        {"hook_event_name": "PreToolUse", "tool_name": tool_name},
        env=env,
        unset=() if headless else ("AUTOSKILLIT_HEADLESS",),
        cwd=Path(cwd) if cwd else tmp_path,
    )


def _assert_reconnect_context(event: dict, emission) -> None:
    assert emission.exit_code == 0, emission.stderr
    payload = json.loads(emission.stdout)
    hook_output = payload["hookSpecificOutput"]
    context = hook_output["additionalContext"]
    assert "/MCP" in context
    for verdict_fn in (codex_verdict, claude_verdict):
        verdict = verdict_fn(
            event["hook_event_name"],
            exit_code=emission.exit_code,
            stdout=emission.stdout,
            stderr=emission.stderr,
        )
        assert verdict.status == STATUS_COMPLETED
        assert verdict.contexts


def _dead_pid(tmp_path: Path) -> int:
    """Return a PID that is guaranteed to not be running.

    Uses ``pid_max + 1`` — a value the Linux kernel can never assign — so no
    PID-reuse race is possible regardless of xdist parallelism or process churn.
    Falls back to spawn-and-reap on non-Linux platforms where ``/proc`` is absent.
    """
    try:
        return int(Path("/proc/sys/kernel/pid_max").read_text()) + 1
    except OSError:
        proc = subprocess.Popen([sys.executable, "-c", "pass"])
        proc.wait()
        return proc.pid


def test_mcp_health_advisor_dead_pid_injects_message(tmp_path: Path) -> None:
    """Dead PID entry for current project → inject reconnection message."""
    dead_pid = _dead_pid(tmp_path)
    kitchens = [
        {
            "kitchen_id": "k1",
            "pid": dead_pid,
            "project_path": str(tmp_path),
            "opened_at": "2026-01-01T00:00:00+00:00",
        }
    ]
    event = {"hook_event_name": "PreToolUse", "tool_name": "Read"}
    _assert_reconnect_context(
        event,
        _run_guard(
            tmp_path,
            {},
            tool_name="Read",
            kitchens=kitchens,
            cwd=tmp_path,
            headless=False,
        ),
    )


@pytest.mark.skipif(sys.platform != "linux", reason="Linux-only: uses os.fork and /proc")
def test_mcp_health_advisor_zombie_pid_injects_message(tmp_path: Path) -> None:
    """Kitchen entry whose PID is a zombie → treated as dead, message injected."""
    child_pid = os.fork()
    if child_pid == 0:
        os._exit(0)
    try:
        deadline = time.time() + 2.0
        while not is_pid_zombie(child_pid) and time.time() < deadline:
            time.sleep(0.01)
        assert is_pid_zombie(child_pid)

        kitchens = [
            {
                "kitchen_id": "k-zombie",
                "pid": child_pid,
                "project_path": str(tmp_path),
                "opened_at": "2026-01-01T00:00:00+00:00",
            }
        ]
        event = {"hook_event_name": "PreToolUse", "tool_name": "Read"}
        _assert_reconnect_context(
            event,
            _run_guard(tmp_path, {}, tool_name="Read", kitchens=kitchens, cwd=tmp_path),
        )
    finally:
        os.waitpid(child_pid, 0)


def test_mcp_health_advisor_no_kitchens_silent(tmp_path: Path) -> None:
    """No active_kitchens.json at all → silent exit 0."""
    emission = _run_guard(tmp_path, {}, tool_name="Bash")
    assert emission.exit_code == 0
    assert emission.stdout == ""


def test_mcp_health_advisor_alive_pid_silent(tmp_path: Path) -> None:
    """Kitchen entry with alive PID → silent exit 0."""
    kitchens = [
        {
            "kitchen_id": "k-alive",
            "pid": os.getpid(),
            "project_path": str(tmp_path),
            "opened_at": "2026-01-01T00:00:00+00:00",
        }
    ]
    emission = _run_guard(tmp_path, {}, tool_name="Read", kitchens=kitchens, cwd=tmp_path)
    assert emission.exit_code == 0
    assert emission.stdout == ""


def test_mcp_health_advisor_headless_bypass(tmp_path: Path) -> None:
    """AUTOSKILLIT_HEADLESS=1 → message suppressed even with dead PID."""
    dead_pid = _dead_pid(tmp_path)
    kitchens = [
        {
            "kitchen_id": "k-headless",
            "pid": dead_pid,
            "project_path": str(tmp_path),
            "opened_at": "2026-01-01T00:00:00+00:00",
        }
    ]
    emission = _run_guard(
        tmp_path,
        {},
        tool_name="Read",
        kitchens=kitchens,
        cwd=tmp_path,
        headless=True,
    )
    assert emission.exit_code == 0
    assert emission.stdout == ""


def test_mcp_health_advisor_no_matching_project(tmp_path: Path) -> None:
    """Kitchen entry for a different project_path → silent exit 0."""
    dead_pid = _dead_pid(tmp_path)
    kitchens = [
        {
            "kitchen_id": "k-other",
            "pid": dead_pid,
            "project_path": "/some/other/project",
            "opened_at": "2026-01-01T00:00:00+00:00",
        }
    ]
    # Run from tmp_path — project_path mismatch → no message
    emission = _run_guard(tmp_path, {}, tool_name="Read", kitchens=kitchens, cwd=tmp_path)
    assert emission.exit_code == 0
    assert emission.stdout == ""


def test_mcp_health_advisor_malformed_json_failopen(tmp_path: Path) -> None:
    """Malformed active_kitchens.json → fail-open (exit 0, empty output)."""
    home = tmp_path / "fakehome"
    home.mkdir(exist_ok=True)
    ak_dir = home / ".autoskillit"
    ak_dir.mkdir(exist_ok=True)
    (ak_dir / "active_kitchens.json").write_text("this is not valid JSON {{{{")

    emission = run_hook(
        "guards/mcp_health_advisor.py",
        "{not valid JSON",
        env={"HOME": str(home), "AUTOSKILLIT_LOG_DIR": str(tmp_path / "logs")},
        unset=("AUTOSKILLIT_HEADLESS",),
        cwd=tmp_path,
    )
    assert emission.exit_code == 0
    assert emission.stdout == ""
