"""PreToolUse hook: blocks native Claude Code tools when the kitchen gate is open.

Matched only against native tool names via the hooks.json matcher regex.
When the gate file exists with a valid lease whose identity matches, denies the call.
Fail-open on any error to avoid blocking normal development.

Three-factor lease identity: PID liveness + starttime_ticks + boot_id + TTL.
All validation uses stdlib only — no autoskillit imports allowed in hooks.
"""

from __future__ import annotations

import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path

GATE_STATE_FILENAME = ".kitchen_gate"
GATE_DIR_COMPONENTS = (".autoskillit", "temp")
LEASE_FIELDS = frozenset({"pid", "starttime_ticks", "boot_id", "opened_at"})
MAX_AGE_HOURS = 24


def _is_pid_alive(pid: int) -> bool:
    """Check if a process with the given PID is running (stdlib only)."""
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # exists but can't signal


def _read_starttime_ticks(pid: int) -> int | None:
    """Read raw starttime ticks (field 22) from /proc/{pid}/stat (stdlib only).

    Parses after the last ')' to handle comm fields with spaces or ')'.
    Returns None on any failure.
    """
    try:
        raw = Path(f"/proc/{pid}/stat").read_text()
    except (FileNotFoundError, OSError):
        return None
    try:
        after_comm = raw[raw.rfind(")") + 1 :]
        parts = after_comm.split()
        return int(parts[19])
    except (IndexError, ValueError):
        return None


def _read_boot_id() -> str | None:
    """Read /proc/sys/kernel/random/boot_id (stdlib only). Returns None on failure."""
    try:
        return Path("/proc/sys/kernel/random/boot_id").read_text().strip()
    except (FileNotFoundError, OSError):
        return None


def _remove_gate(gate_path: Path) -> None:
    """Remove the gate file, silently ignoring errors."""
    try:
        gate_path.unlink(missing_ok=True)
    except OSError:
        pass


def main() -> None:
    gate_path = Path.cwd().joinpath(*GATE_DIR_COMPONENTS, GATE_STATE_FILENAME)

    try:
        _event = json.loads(sys.stdin.read())
    except (json.JSONDecodeError, ValueError, OSError):
        sys.exit(0)

    if not gate_path.exists():
        sys.exit(0)

    try:
        data = json.loads(gate_path.read_text())
        pid = data["pid"]
    except (json.JSONDecodeError, KeyError, TypeError, OSError, ValueError):
        _remove_gate(gate_path)
        sys.exit(0)

    if not _is_pid_alive(pid):
        _remove_gate(gate_path)
        sys.exit(0)

    lease_boot_id = data.get("boot_id")
    if lease_boot_id is not None:
        current_boot_id = _read_boot_id()
        if current_boot_id is not None and lease_boot_id != current_boot_id:
            _remove_gate(gate_path)
            sys.exit(0)

    lease_ticks = data.get("starttime_ticks")
    if lease_ticks is not None:
        current_ticks = _read_starttime_ticks(pid)
        if current_ticks is None:
            _remove_gate(gate_path)
            sys.exit(0)
        if lease_ticks != current_ticks:
            _remove_gate(gate_path)
            sys.exit(0)

    opened_at_str = data.get("opened_at")
    if opened_at_str is not None:
        try:
            opened_at = datetime.fromisoformat(opened_at_str)
            if opened_at.tzinfo is None:
                opened_at = opened_at.replace(tzinfo=UTC)
            age_hours = (datetime.now(UTC) - opened_at).total_seconds() / 3600
            if age_hours > MAX_AGE_HOURS:
                _remove_gate(gate_path)
                sys.exit(0)
        except (ValueError, TypeError):
            pass

    print(
        json.dumps(
            {
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": "deny",
                    "permissionDecisionReason": (
                        "Native tools are prohibited during pipeline execution. "
                        "Use run_skill for code investigation "
                        "and run_cmd for shell commands."
                    ),
                }
            }
        )
    )
    sys.exit(0)


if __name__ == "__main__":
    main()
