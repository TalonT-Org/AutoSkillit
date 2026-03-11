#!/usr/bin/env python3
"""PreToolUse hook — blocks orchestration tools from headless sessions.

Headless sessions (AUTOSKILLIT_HEADLESS=1) must never call run_skill,
run_cmd, or run_python. This is defense-in-depth over the in-handler
gate check in each tool.

The two-tier invariant: Orchestrator (Tier 1) spawns workers via run_skill.
Workers (Tier 2) execute skills using native Claude Code tools. No nesting.
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

    if os.environ.get("AUTOSKILLIT_HEADLESS") != "1":
        sys.exit(0)  # not headless — pass through

    tool_name: str = data.get("tool_name", "")
    # MCP tool names are prefixed: mcp__<server>__<tool>
    # Check each __ segment against the orchestration tool set
    for part in tool_name.split("__"):
        if part in _ORCHESTRATION_TOOLS:
            payload = json.dumps(
                {
                    "hookSpecificOutput": {
                        "hookEventName": "PreToolUse",
                        "permissionDecision": "deny",
                        "permissionDecisionReason": (
                            f"{part} cannot be called from headless sessions. "
                            "Only the Tier 1 orchestrator may call orchestration tools. "
                            "Headless workers (Tier 2) execute skills via native Claude Code tools."
                        ),
                    }
                }
            )
            sys.stdout.write(payload + "\n")
            sys.exit(0)

    sys.exit(0)


if __name__ == "__main__":
    main()
