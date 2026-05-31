#!/usr/bin/env python3
"""PreToolUse hook — blocks run_in_background=true in skill sessions (ADR-0001).

Background execution causes race conditions and lost results. Skill sessions
must use foreground execution only. Multiple foreground tool calls in a single
message execute concurrently without this risk.
"""

import json
import os
import sys

BACKGROUND_EXEC_DENY_TRIGGER: str = "run_in_background=true is prohibited in skill sessions"


def main() -> None:
    try:
        data = json.loads(sys.stdin.read())
    except (json.JSONDecodeError, ValueError, OSError):
        sys.exit(0)  # fail-open on malformed input

    # Interactive sessions always pass
    if os.environ.get("AUTOSKILLIT_HEADLESS") != "1":
        sys.exit(0)

    # Headless: resolve session type, fail-closed to skill session
    raw_session_type = os.environ.get("AUTOSKILLIT_SESSION_TYPE", "")
    session_type = raw_session_type.lower()
    if session_type in ("orchestrator", "fleet"):
        sys.exit(0)  # permitted tiers
    _unrecognized_tier = bool(session_type) and session_type != "skill"

    tool_input = data.get("tool_input")
    if not isinstance(tool_input, dict):
        sys.exit(0)  # fail-open: missing or malformed tool_input

    if tool_input.get("run_in_background"):
        denial_reason = (
            f"{BACKGROUND_EXEC_DENY_TRIGGER} (ADR-0001). "
            "Background execution causes race conditions and lost results. "
            "Use foreground execution — multiple tool calls in a single message "
            "execute concurrently."
        )
        if _unrecognized_tier:
            denial_reason += (
                f" (AUTOSKILLIT_SESSION_TYPE={raw_session_type!r} is not a recognized tier;"
                " expected: orchestrator, fleet, or skill)"
            )
        payload = json.dumps(
            {
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": "deny",
                    "permissionDecisionReason": denial_reason,
                }
            }
        )
        sys.stdout.write(payload + "\n")

    sys.exit(0)


if __name__ == "__main__":
    main()
