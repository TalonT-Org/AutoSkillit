#!/usr/bin/env python3
"""PreToolUse hook — blocks orchestration tools from L1 skill sessions.

Skill sessions (AUTOSKILLIT_SESSION_TYPE=skill or unset in headless mode) must
never call run_skill, run_cmd, or run_python. This is defense-in-depth over
the in-handler gate check in each tool.

L3+ invariant: orchestrator (L3) and fleet (L3) sessions may call orchestration tools.
Skill sessions use native Claude Code tools only.
"""

import json
import os
import sys

SKILL_ORCHESTRATION_DENY_TRIGGER: str = "cannot be called from skill sessions"

_ORCHESTRATION_TOOLS: frozenset[str] = frozenset({"run_skill", "run_cmd", "run_python"})


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
        sys.exit(0)  # permitted tiers — not a skill session
    # skill, leaf (deprecated), unset → deny below; unrecognized non-empty values also denied
    _unrecognized_tier = bool(session_type) and session_type not in ("skill", "leaf")

    tool_name: str = data.get("tool_name", "")
    # MCP tool names are prefixed: mcp__<server>__<tool>
    # Check only the last __ segment — avoids false positives where a server
    # name coincidentally contains an orchestration tool name.
    tool = tool_name.split("__")[-1]
    if tool in _ORCHESTRATION_TOOLS:
        denial_reason = (
            f"{tool} cannot be called from skill sessions. "
            "Only orchestrator or fleet sessions may call orchestration tools. "
            "Skill sessions use native Claude Code tools only."
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

    sys.exit(0)


if __name__ == "__main__":
    main()
