"""PreToolUse hook: blocks Write/Edit outside the allowed prefix in read-only sessions."""

from __future__ import annotations

import json
import os
import sys

WRITE_GUARD_DENY_TRIGGER = "read-only skill session"


def _deny(reason: str) -> None:
    json.dump(
        {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": reason,
            }
        },
        sys.stdout,
    )
    sys.exit(0)


def main() -> None:
    if not os.environ.get("AUTOSKILLIT_HEADLESS"):
        sys.exit(0)

    allowed_prefix = os.environ.get("AUTOSKILLIT_ALLOWED_WRITE_PREFIX", "")
    if not allowed_prefix:
        sys.exit(0)

    try:
        data = json.loads(sys.stdin.read())
    except (json.JSONDecodeError, ValueError, OSError):
        _deny(f"Write/Edit blocked: {WRITE_GUARD_DENY_TRIGGER} (malformed hook input).")
        return

    tool_name = data.get("tool_name", "")
    if tool_name not in ("Write", "Edit"):
        sys.exit(0)

    tool_input = data.get("tool_input", {})
    file_path = tool_input.get("file_path", "")
    if not file_path:
        _deny(f"Write/Edit blocked: {WRITE_GUARD_DENY_TRIGGER} (no file_path).")
        return

    resolved = os.path.realpath(file_path)
    real_prefix = os.path.realpath(allowed_prefix)
    norm_prefix = real_prefix.rstrip("/") + "/"

    if resolved.startswith(norm_prefix) or resolved == norm_prefix.rstrip("/"):
        sys.exit(0)

    _deny(
        f"Write/Edit blocked: {WRITE_GUARD_DENY_TRIGGER}. "
        f"Only writes to {allowed_prefix} are permitted."
    )


if __name__ == "__main__":
    main()
