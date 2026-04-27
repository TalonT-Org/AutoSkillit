#!/usr/bin/env python3
"""PreToolUse hook: branch protection guard for merge_worktree and push_to_remote.

Reads AUTOSKILLIT_PROTECTED_BRANCHES env var (comma-separated, default:
main,integration,stable). Denies tool calls that target a protected branch.

Matched tools:
  - merge_worktree: checks base_branch parameter
  - push_to_remote: checks branch parameter
"""

import json
import os
import sys

BRANCH_PROTECTION_DENY_TRIGGER: str = "Branch '"


def main() -> None:
    raw = sys.stdin.read().strip()
    if not raw:
        return

    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return

    tool_name = payload.get("tool_name", "")
    tool_input = payload.get("tool_input", {})

    # Determine which parameter to check based on tool name
    if "merge_worktree" in tool_name:
        branch = tool_input.get("base_branch", "")
    elif "push_to_remote" in tool_name:
        branch = tool_input.get("branch", "")
    else:
        return  # Not a matched tool

    if not branch:
        return

    # Read protected branches from env (comma-separated)
    env_val = os.environ.get("AUTOSKILLIT_PROTECTED_BRANCHES", "main,integration,stable")
    protected = [b.strip() for b in env_val.split(",") if b.strip()]

    if branch in protected:
        result = {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": (
                    f"Branch '{branch}' is protected. Protected branches: {protected}"
                ),
            }
        }
        print(json.dumps(result))


if __name__ == "__main__":
    main()
