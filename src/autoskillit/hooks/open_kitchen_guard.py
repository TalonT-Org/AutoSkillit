#!/usr/bin/env python3
"""PreToolUse hook — blocks open_kitchen from headless sessions and writes kitchen marker.

Headless sessions launched by run_skill have AUTOSKILLIT_HEADLESS=1 in their
environment. This hook denies open_kitchen calls from those sessions, enforcing
that only humans (via /autoskillit:open-kitchen) can open the kitchen.

On the permit path (non-headless), writes a kitchen-open session marker so that
ask_user_question_guard can verify the kitchen is open before allowing AskUserQuestion.
"""

import json
import os
import sys


def _write_kitchen_marker(session_id: str, recipe_name: str | None) -> None:
    """Write the kitchen-open session marker via the canonical kitchen_state module."""
    from autoskillit.core.kitchen_state import write_marker  # noqa: PLC0415

    write_marker(session_id, recipe_name)


def main() -> None:
    try:
        data = json.loads(sys.stdin.read())
    except (json.JSONDecodeError, ValueError, OSError):
        sys.exit(0)  # fail-open on malformed input or broken pipe

    if os.environ.get("AUTOSKILLIT_HEADLESS") == "1":
        payload = json.dumps(
            {
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": "deny",
                    "permissionDecisionReason": (
                        "open_kitchen cannot be called from headless sessions. "
                        "Open the kitchen in your human session using /autoskillit:open-kitchen."
                    ),
                }
            }
        )
        sys.stdout.write(payload + "\n")
        sys.exit(0)

    # Permit path: write a kitchen-open session marker so ask_user_question_guard
    # can verify the kitchen is open before allowing AskUserQuestion.
    # The marker is written here (from the PreToolUse hook) rather than from the
    # MCP server tool because the hook receives the Claude Code session_id on stdin;
    # the FastMCP Context does not expose it.
    try:
        session_id = data.get("session_id", "")
        recipe_name: str | None = None
        tool_input = data.get("tool_input") or {}
        if isinstance(tool_input, dict):
            recipe_name = tool_input.get("name") or None
        if session_id:
            _write_kitchen_marker(session_id, recipe_name)
    except Exception as e:
        print(f"[open_kitchen_guard] marker write failed: {e}", file=sys.stderr)
        # Surface the failure so the user knows AskUserQuestion will be blocked
        # in headless sub-sessions (ask_user_question_guard relies on the marker).
        payload = json.dumps(
            {
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "message": (
                        f"Warning: kitchen marker write failed ({e}). "
                        "AskUserQuestion may be blocked in headless sub-sessions."
                    ),
                }
            }
        )
        sys.stdout.write(payload + "\n")

    sys.exit(0)


if __name__ == "__main__":
    main()
