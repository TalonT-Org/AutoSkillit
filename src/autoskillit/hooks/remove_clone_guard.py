#!/usr/bin/env python3
"""PreToolUse hook: block automatic remove_clone calls.

Denies any remove_clone call where keep != "true".
Clones are never removed automatically — the user removes them
manually when the pipeline is fully complete.
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
                        "permissionDecision": "deny",
                        "permissionDecisionReason": (
                            f"remove_clone is blocked by the AutoSkillit guard. "
                            f"Clones are never removed automatically. "
                            f"The clone at {clone_path} is preserved — "
                            f"remove it manually when done: rm -rf {clone_path}"
                        ),
                    }
                }
            )
        )
    sys.exit(0)


if __name__ == "__main__":
    main()
