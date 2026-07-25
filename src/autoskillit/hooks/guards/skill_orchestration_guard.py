#!/usr/bin/env python3
"""PreToolUse hook — enforces exact orchestration roles for execution tools.

Skill sessions (AUTOSKILLIT_SESSION_TYPE=skill or unset in headless mode) must
never call run_skill, run_cmd, or run_python. This is defense-in-depth over
the in-handler gate check in each tool.

L2 orchestrators may call all three tools. L3 fleet sessions retain run_cmd and
run_python, but must dispatch L2 work through dispatch_food_truck rather than
calling run_skill directly. Skill sessions use native Claude Code tools only.
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

    tool_name: str = data.get("tool_name", "")
    # MCP tool names are prefixed: mcp__<server>__<tool>
    # Check only the last __ segment — avoids false positives where a server
    # name coincidentally contains an orchestration tool name.
    tool = tool_name.split("__")[-1]
    if tool not in _ORCHESTRATION_TOOLS:
        sys.exit(0)

    # Headless: resolve session type, fail-closed for orchestration tools.
    raw_session_type = os.environ.get("AUTOSKILLIT_SESSION_TYPE", "")
    session_type = raw_session_type.lower()
    if session_type == "orchestrator":
        sys.exit(0)
    if session_type == "fleet" and tool in {"run_cmd", "run_python"}:
        sys.exit(0)

    # skill, unset → deny below; unrecognized non-empty values also denied
    _unrecognized_tier = bool(session_type) and session_type != "skill"

    if session_type == "fleet":
        denial_reason = (
            "run_skill cannot be called from fleet sessions. "
            "Only orchestrator sessions may call run_skill. "
            "Fleet sessions create orchestrators with dispatch_food_truck."
        )
    else:
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


if __name__ == "__main__":
    main()
