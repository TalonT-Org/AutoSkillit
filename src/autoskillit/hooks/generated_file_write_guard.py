#!/usr/bin/env python3
"""
PreToolUse hook — denies Write and Edit calls targeting generated files
(hooks.json and .claude/settings.json). These files are machine-local
generated artifacts managed by 'autoskillit install'. Direct edits bypass
hook_registry.py and create ghost entries that cause ENOENT fatal denials.
"""

import json
import os
import sys

_GENERATED_FILE_SUFFIXES = ("hooks.json", ".claude/settings.json")


def main() -> None:
    try:
        data = json.loads(sys.stdin.read())
    except (json.JSONDecodeError, ValueError, OSError):
        sys.exit(0)

    tool_name = data.get("tool_name", "")
    if tool_name not in ("Write", "Edit"):
        sys.exit(0)

    file_path = data.get("tool_input", {}).get("file_path", "")
    if not isinstance(file_path, str) or not file_path:
        sys.exit(0)

    # Normalize to forward slashes for cross-platform suffix matching
    normalized = file_path.replace(os.sep, "/")
    if not any(normalized.endswith(suffix) for suffix in _GENERATED_FILE_SUFFIXES):
        sys.exit(0)

    payload = json.dumps(
        {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": (
                    f"'{file_path}' is a generated file with machine-local absolute paths. "
                    f"Direct edits bypass hook_registry.py and create ghost hook entries "
                    f"that block all tool calls with ENOENT. "
                    f"Use 'autoskillit install' to regenerate, or "
                    f"'autoskillit init' to sync settings.json."
                ),
            }
        }
    )
    sys.stdout.write(payload + "\n")
    sys.exit(0)


if __name__ == "__main__":
    main()
