#!/usr/bin/env python3
"""PreToolUse hook — blocks open_kitchen from headless sessions.

Headless sessions launched by run_skill have AUTOSKILLIT_HEADLESS=1 in their
environment. This hook denies open_kitchen calls from those sessions, enforcing
that only humans (via /autoskillit:open-kitchen) can open the kitchen.
"""
import json
import os
import sys


def main() -> None:
    try:
        json.loads(sys.stdin.read())
    except (json.JSONDecodeError, ValueError):
        sys.exit(0)  # fail-open on malformed input

    if os.environ.get("AUTOSKILLIT_HEADLESS") == "1":
        payload = json.dumps(
            {
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": "deny",
                    "permissionDecisionReason": (
                        "open_kitchen cannot be called from headless sessions. "
                        "Open the kitchen in your human session using /autoskillit:open-kitchen."
                    ),
                }
            }
        )
        sys.stdout.write(payload + "\n")
    sys.exit(0)


if __name__ == "__main__":
    main()
