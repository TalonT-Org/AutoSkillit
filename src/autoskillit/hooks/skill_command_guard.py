"""PreToolUse hook: block run_skill/run_skill_retry calls with non-slash skill_command.

Protocol: read PreToolUse JSON event from stdin, write decision JSON to stdout, exit 0.
Fail-open: any error approves silently (never block Claude Code).
"""
from __future__ import annotations

import json
import sys


def main() -> None:
    try:
        raw = sys.stdin.read()
        event = json.loads(raw)
        tool_input = event.get("tool_input", {})
        skill_command = str(tool_input.get("skill_command", "/")).strip()
    except Exception:
        sys.exit(0)  # fail-open: malformed event → approve

    if not skill_command.startswith("/"):
        print(
            json.dumps(
                {
                    "hookSpecificOutput": {
                        "hookEventName": "PreToolUse",
                        "permissionDecision": "deny",
                        "permissionDecisionReason": (
                            "run_skill requires a slash-command prefix.\n"
                            f"Got: {skill_command!r}\n"
                            "Expected: skill_command must start with '/' "
                            "(e.g. /autoskillit:investigate, /autoskillit:make-plan, /audit-arch).\n"
                            "Arbitrary prose prompts are not valid skill invocations."
                        ),
                    }
                }
            )
        )
    sys.exit(0)


if __name__ == "__main__":
    main()
