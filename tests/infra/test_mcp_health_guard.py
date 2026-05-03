"""Tests for mcp_health_guard PreToolUse hook."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from autoskillit.core.paths import pkg_root


def _run_guard(
    tmp_path: Path,
    env_extra: dict,
    tool_name: str = "Read",
    kitchens: list[dict] | None = None,
    cwd: Path | str | None = None,
    headless: bool = False,
) -> tuple[int, dict]:
    """Run mcp_health_guard.py as a subprocess, return (returncode, parsed_stdout)."""
    hook_path = pkg_root() / "hooks" / "guards" / "mcp_health_guard.py"
    home = tmp_path / "fakehome"
    home.mkdir(exist_ok=True)
    ak_dir = home / ".autoskillit"
    ak_dir.mkdir(exist_ok=True)
    if kitchens is not None:
        (ak_dir / "active_kitchens.json").write_text(
            json.dumps({"kitchens": kitchens, "schema_version": 1})
        )
    env = {k: v for k, v in os.environ.items() if k != "AUTOSKILLIT_HEADLESS"}
    env["HOME"] = str(home)
    if headless:
        env["AUTOSKILLIT_HEADLESS"] = "1"
    env.update(env_extra)
    result = subprocess.run(
        [sys.executable, str(hook_path)],
        input=json.dumps({"tool_name": tool_name}),
        capture_output=True,
        text=True,
        env=env,
        timeout=10,
        cwd=str(cwd) if cwd else None,
    )
    parsed = json.loads(result.stdout) if result.stdout.strip() else {}
    return result.returncode, parsed


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


def test_mcp_health_guard_dead_pid_injects_message(tmp_path: Path) -> None:
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
    returncode, payload = _run_guard(
        tmp_path, {}, tool_name="Read", kitchens=kitchens, cwd=tmp_path, headless=False
    )
    assert returncode == 0
    hook_out = payload.get("hookSpecificOutput", {})
    assert "/MCP" in hook_out.get("message", ""), (
        f"Expected /MCP reconnect hint in message, got: {hook_out}"
    )


def test_mcp_health_guard_no_kitchens_silent(tmp_path: Path) -> None:
    """No active_kitchens.json at all → silent exit 0."""
    returncode, payload = _run_guard(tmp_path, {}, tool_name="Bash")
    assert returncode == 0
    assert payload == {}, f"Expected empty output, got: {payload}"


def test_mcp_health_guard_alive_pid_silent(tmp_path: Path) -> None:
    """Kitchen entry with alive PID → silent exit 0."""
    kitchens = [
        {
            "kitchen_id": "k-alive",
            "pid": os.getpid(),
            "project_path": str(tmp_path),
            "opened_at": "2026-01-01T00:00:00+00:00",
        }
    ]
    returncode, payload = _run_guard(
        tmp_path, {}, tool_name="Read", kitchens=kitchens, cwd=tmp_path
    )
    assert returncode == 0
    assert payload == {}, f"Expected empty output for alive PID, got: {payload}"


def test_mcp_health_guard_headless_bypass(tmp_path: Path) -> None:
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
    returncode, payload = _run_guard(
        tmp_path,
        {},
        tool_name="Read",
        kitchens=kitchens,
        cwd=tmp_path,
        headless=True,
    )
    assert returncode == 0
    assert payload == {}, f"Expected empty output in headless mode, got: {payload}"


def test_mcp_health_guard_no_matching_project(tmp_path: Path) -> None:
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
    returncode, payload = _run_guard(
        tmp_path, {}, tool_name="Read", kitchens=kitchens, cwd=tmp_path
    )
    assert returncode == 0
    assert payload == {}, f"Expected empty output for non-matching project, got: {payload}"


def test_mcp_health_guard_malformed_json_failopen(tmp_path: Path) -> None:
    """Malformed active_kitchens.json → fail-open (exit 0, empty output)."""
    home = tmp_path / "fakehome"
    home.mkdir(exist_ok=True)
    ak_dir = home / ".autoskillit"
    ak_dir.mkdir(exist_ok=True)
    (ak_dir / "active_kitchens.json").write_text("this is not valid JSON {{{{")

    hook_path = pkg_root() / "hooks" / "guards" / "mcp_health_guard.py"
    env = {**os.environ, "HOME": str(home)}
    result = subprocess.run(
        [sys.executable, str(hook_path)],
        input=json.dumps({"tool_name": "Read"}),
        capture_output=True,
        text=True,
        env=env,
        timeout=10,
    )
    assert result.returncode == 0
    assert not result.stdout.strip(), (
        f"Expected empty output on malformed JSON, got: {result.stdout!r}"
    )
