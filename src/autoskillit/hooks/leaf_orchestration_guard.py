#!/usr/bin/env python3
"""PreToolUse hook — blocks orchestration tools from leaf-tier sessions.

Leaf sessions (AUTOSKILLIT_SESSION_TYPE=leaf or unset in headless mode) must
never call run_skill, run_cmd, or run_python. This is defense-in-depth over
the in-handler gate check in each tool.

Tier invariant: orchestrator and fleet tiers may call orchestration tools.
Leaf workers use native Claude Code tools only.
"""

import json
import os
import sys

_ORCHESTRATION_TOOLS: frozenset[str] = frozenset({"run_skill", "run_cmd", "run_python"})


def main() -> None:
    try:
        data = json.loads(sys.stdin.read())
    except (json.JSONDecodeError, ValueError, OSError):
        sys.exit(0)  # fail-open on malformed input

    # Interactive sessions always pass
    if os.environ.get("AUTOSKILLIT_HEADLESS") != "1":
        sys.exit(0)

    # Headless: resolve session type, fail-closed to leaf
    session_type = os.environ.get("AUTOSKILLIT_SESSION_TYPE", "").lower()
    if session_type in ("orchestrator", "fleet"):
        sys.exit(0)  # permitted tiers — not a leaf
    # leaf, unset, or invalid → deny below

    tool_name: str = data.get("tool_name", "")
    # MCP tool names are prefixed: mcp__<server>__<tool>
    # Check only the last __ segment — avoids false positives where a server
    # name coincidentally contains an orchestration tool name.
    tool = tool_name.split("__")[-1]
    if tool in _ORCHESTRATION_TOOLS:
        payload = json.dumps(
            {
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": "deny",
                    "permissionDecisionReason": (
                        f"{tool} cannot be called from leaf sessions. "
                        "Only orchestrator or fleet sessions may call orchestration tools. "
                        "Leaf workers use native Claude Code tools only."
                    ),
                }
            }
        )
        sys.stdout.write(payload + "\n")
        sys.exit(0)

    sys.exit(0)


if __name__ == "__main__":
    main()
