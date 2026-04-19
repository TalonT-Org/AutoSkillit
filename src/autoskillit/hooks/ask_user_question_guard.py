#!/usr/bin/env python3
"""PreToolUse hook — blocks AskUserQuestion before kitchen is open.

Reads the kitchen-open session marker for the current session. If no fresh
marker exists, denies the call with a structured hookSpecificOutput envelope
containing the ToolSearch recovery hint.

Stdlib-only — runs under any Python interpreter without the autoskillit package.
"""

from __future__ import annotations

import json
import os
import sys
from datetime import UTC
from pathlib import Path


def _get_marker_path(session_id: str) -> Path:
    override = os.environ.get("AUTOSKILLIT_STATE_DIR")
    if override:
        return Path(override) / "kitchen_state" / f"{session_id}.json"
    campaign_id = os.environ.get("AUTOSKILLIT_CAMPAIGN_ID", "")
    base = Path.cwd() / ".autoskillit" / "temp" / "kitchen_state"
    state_dir = base / campaign_id if campaign_id else base
    return state_dir / f"{session_id}.json"


def _read_marker(session_id: str) -> dict | None:
    try:
        path = _get_marker_path(session_id)
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _is_fresh(data: dict, ttl_hours: int = 24) -> bool:
    try:
        from datetime import datetime

        opened_at = datetime.fromisoformat(data["opened_at"])
        age = datetime.now(UTC) - opened_at
        return age.total_seconds() < ttl_hours * 3600
    except (KeyError, ValueError, TypeError):
        return False


def main() -> None:
    try:
        payload = json.loads(sys.stdin.read())
    except (json.JSONDecodeError, ValueError, OSError):
        sys.exit(0)  # fail-open on malformed input

    tool_name = payload.get("tool_name", "")
    if tool_name != "AskUserQuestion":
        sys.exit(0)  # defensive; matcher should pre-filter

    # In interactive sessions, AskUserQuestion is always permitted --
    # the user is present at the terminal and can respond.
    if os.environ.get("AUTOSKILLIT_HEADLESS") != "1":
        sys.exit(0)  # not headless -- pass through

    session_id = payload.get("session_id", "")
    if not session_id:
        sys.exit(0)  # fail-open when session_id unavailable

    marker = _read_marker(session_id)
    # NOTE: The in-hook freshness check below is the SOLE source of correctness.
    # The session_start_hook TTL sweep is disk hygiene only — it has race windows.
    # A future maintainer MUST NOT remove this check on the grounds that "the sweep
    # handles stale markers": the sweep does not.
    if marker is not None and _is_fresh(marker):
        sys.exit(0)  # kitchen is open — permit AskUserQuestion

    deny_payload = json.dumps(
        {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": (
                    "AskUserQuestion is not available in headless sessions without "
                    "an open kitchen. If this is a pipeline worker, proceed without "
                    "user confirmation or use a default/fallback behavior. "
                    "If an orchestrator session, call open_kitchen first."
                ),
            }
        }
    )
    sys.stdout.write(deny_payload + "\n")
    sys.exit(0)


if __name__ == "__main__":
    main()
