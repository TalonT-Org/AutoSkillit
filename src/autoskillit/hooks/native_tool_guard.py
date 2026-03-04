"""PreToolUse hook: blocks native Claude Code tools when the kitchen gate is open.

Matched only against native tool names via the hooks.json matcher regex.
When the gate file exists (kitchen is open), denies the call. When absent, allows it.
Fail-open on any error to avoid blocking normal development.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

GATE_STATE_FILENAME = ".kitchen_gate"


def main() -> None:
    gate_path = Path.cwd() / "temp" / GATE_STATE_FILENAME

    # Parse stdin — fail-open on any error
    try:
        _event = json.loads(sys.stdin.read())
    except (json.JSONDecodeError, ValueError, OSError):
        sys.exit(0)

    if not gate_path.exists():
        sys.exit(0)  # Kitchen closed — allow

    # Kitchen is open — deny native tool
    print(
        json.dumps(
            {
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": "deny",
                    "permissionDecisionReason": (
                        "Native tools are prohibited during pipeline execution. "
                        "Use run_skill/run_skill_retry for code investigation "
                        "and run_cmd for shell commands."
                    ),
                }
            }
        )
    )
    sys.exit(0)


if __name__ == "__main__":
    main()
