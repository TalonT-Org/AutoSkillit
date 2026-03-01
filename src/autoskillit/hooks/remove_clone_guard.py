#!/usr/bin/env python3
"""PreToolUse hook: guard automatic remove_clone calls.

Prompts the user for permission on any remove_clone call where keep != "true".
Clones are never removed automatically — the user must approve each removal.
"""

import json
import sys


def main() -> None:
    try:
        event = json.loads(sys.stdin.read())
    except Exception:
        sys.exit(0)  # malformed event — approve

    tool_input = event.get("tool_input", {})
    keep = str(tool_input.get("keep", "false")).strip().lower()
    clone_path = tool_input.get("clone_path", "<unknown>")

    if keep != "true":
        print(
            json.dumps(
                {
                    "hookSpecificOutput": {
                        "hookEventName": "PreToolUse",
                        "permissionDecision": "askPermission",
                        "permissionDecisionReason": (
                            f"remove_clone wants to delete the clone at {clone_path}. "
                            f"Approve only if the pipeline is fully complete and you "
                            f"no longer need this clone."
                        ),
                    }
                }
            )
        )
    sys.exit(0)


if __name__ == "__main__":
    main()
