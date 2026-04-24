#!/usr/bin/env python3
"""PreToolUse hook — detects MCP server disconnection and suggests /MCP reconnect.

Reads ~/.autoskillit/active_kitchens.json to find kitchen entries for the current
project. If entries exist but all PIDs are dead, injects an informational message
via hookSpecificOutput.message. Never blocks tool execution.

SIGHUP treated as shutdown: when a terminal disconnect kills the MCP server,
active_kitchens.json still holds the dead PID. This hook surfaces that state to
the user on the next native tool call, prompting reconnection before the session
tries to use kitchen tools that no longer exist.

Stdlib-only — runs under any Python interpreter without the autoskillit package.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path


def _active_kitchens_path() -> Path:
    return Path.home() / ".autoskillit" / "active_kitchens.json"


def _read_kitchens() -> list[dict]:
    """Read and return kitchen entries from active_kitchens.json."""
    try:
        data = json.loads(_active_kitchens_path().read_text(encoding="utf-8"))
        entries = data.get("kitchens", [])
        return entries if isinstance(entries, list) else []
    except (OSError, json.JSONDecodeError, AttributeError):
        return []


def _pid_alive(pid: int) -> bool:
    """Check if a PID is still running (stdlib-only, no create_time validation)."""
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except (PermissionError, OSError):
        # PermissionError means the process exists but we lack permission to signal it.
        return True


def main() -> None:
    # Fail-open on malformed stdin
    try:
        json.loads(sys.stdin.read())
    except (json.JSONDecodeError, ValueError, OSError):
        sys.exit(0)

    # Never fire in headless sessions — the orchestrator handles reconnection itself.
    if os.environ.get("AUTOSKILLIT_HEADLESS") == "1":
        sys.exit(0)

    cwd = os.getcwd()
    entries = _read_kitchens()

    # Filter to entries matching the current project
    matching = [
        e for e in entries
        if isinstance(e, dict) and e.get("project_path") == cwd
    ]

    if not matching:
        sys.exit(0)  # No kitchen registered for this project

    # Check if ANY matching PID is still alive
    for entry in matching:
        pid = entry.get("pid")
        if isinstance(pid, int) and _pid_alive(pid):
            sys.exit(0)  # Server is alive — no disconnect

    # All matching PIDs are dead — server disconnected
    payload = json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "message": (
                "AutoSkillit MCP server appears disconnected — all registered "
                "server PIDs for this project are dead. Kitchen state has been "
                "lost. Ask the user to run /MCP to reconnect, then re-open "
                "the kitchen with open_kitchen."
            ),
        }
    })
    sys.stdout.write(payload + "\n")
    sys.exit(0)


if __name__ == "__main__":
    main()
