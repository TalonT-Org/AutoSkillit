#!/usr/bin/env python3
"""PreToolUse hook — blocks dispatch_food_truck from headless callers.

Defense-in-depth: dispatch_food_truck must never be called from a headless
session regardless of SESSION_TYPE. This closes L3→L3 recursion where a
franchise session spawns another franchise session via dispatch.

Interactive callers (cook with kitchen open) are always permitted.
"""

import json
import os
import sys


def main() -> None:
    try:
        data = json.loads(sys.stdin.read())
    except (json.JSONDecodeError, ValueError, OSError):
        sys.exit(0)  # fail-open on malformed input

    # Interactive sessions always pass
    if os.environ.get("AUTOSKILLIT_HEADLESS") != "1":
        sys.exit(0)

    # Headless: check if this is dispatch_food_truck
    tool_name: str = data.get("tool_name", "")
    tool = tool_name.split("__")[-1]
    if tool == "dispatch_food_truck":
        payload = json.dumps(
            {
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": "deny",
                    "permissionDecisionReason": (
                        "dispatch_food_truck cannot be called from headless sessions. "
                        "This tool is only available to interactive callers (cook). "
                        "Headless dispatch would create recursive L3 sessions."
                    ),
                }
            }
        )
        sys.stdout.write(payload + "\n")
        sys.exit(0)

    sys.exit(0)


if __name__ == "__main__":
    main()
