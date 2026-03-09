"""PreToolUse hook: blocks native Claude Code tools when the kitchen gate is open.

Matched only against native tool names via the hooks.json matcher regex.
When the gate file exists with a valid lease whose PID is alive, denies the call.
Fail-open on any error to avoid blocking normal development.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

GATE_STATE_FILENAME = ".kitchen_gate"
GATE_DIR_COMPONENTS = (".autoskillit", "temp")


def _is_pid_alive(pid: int) -> bool:
    """Check if a process with the given PID is running (stdlib only)."""
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # exists but can't signal


def main() -> None:
    gate_path = Path.cwd().joinpath(*GATE_DIR_COMPONENTS, GATE_STATE_FILENAME)

    # Parse stdin — fail-open on any error
    try:
        _event = json.loads(sys.stdin.read())
    except (json.JSONDecodeError, ValueError, OSError):
        sys.exit(0)

    if not gate_path.exists():
        sys.exit(0)  # Kitchen closed — allow

    # Read and validate the gate lease
    try:
        data = json.loads(gate_path.read_text())
        pid = data["pid"]
    except (json.JSONDecodeError, KeyError, TypeError, OSError, ValueError):
        # Malformed or unreadable — fail-open, remove stale file
        try:
            gate_path.unlink(missing_ok=True)
        except OSError:
            pass
        sys.exit(0)

    if not _is_pid_alive(pid):
        # Owning process is dead — stale lease
        try:
            gate_path.unlink(missing_ok=True)
        except OSError:
            pass
        sys.exit(0)

    # PID is alive — gate is valid, deny native tool
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
