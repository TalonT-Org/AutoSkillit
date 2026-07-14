#!/usr/bin/env python3
"""PostToolUse hook: maintain a per-session quota-disable marker.

After a successful ``disable_quota_guard`` call, write a marker file keyed
by the caller's ``session_id`` so the PreToolUse / PostToolUse quota hooks
can short-circuit enforcement for that exact session only. After a
successful ``close_kitchen`` call, delete only that session's marker.

Stdlib-only — runs under any Python interpreter without the autoskillit package.
"""

from __future__ import annotations

import json
import sys
from datetime import UTC, datetime
from pathlib import Path

# Sibling-import bootstrap: hooks run as ``python3 /path/to/script.py`` subprocesses
# outside the autoskillit venv. Placing the script's directory first on sys.path
# lets the bare-name import below resolve to the shared stdlib-only settings
# module in both subprocess and package-mode invocations.
_HOOKS_DIR = str(Path(__file__).resolve().parent)
if _HOOKS_DIR not in sys.path:
    sys.path.insert(0, _HOOKS_DIR)

from _hook_settings import (  # noqa: E402
    _atomic_write_marker,
    clear_quota_disable_marker,
    quota_disable_marker_path,
)  # type: ignore[import-not-found]

_HANDLED_TOOLS = frozenset({"disable_quota_guard", "close_kitchen"})

_FAILURE_REWRITE = (
    "quota_guard_state_post_hook: failed to write session-disable marker. "
    "The MCP tool reported success but quota bypass was not persisted; treat "
    "this run_skill as still subject to the configured quota enforcement."
)


def _is_disable_success(tool_response: str) -> bool:
    """Return True iff the disable_quota_guard response indicates success (double-JSON unwrap)."""
    try:
        outer = json.loads(tool_response)
        if not isinstance(outer, dict):
            return False
        result = outer.get("result", "")
        if isinstance(result, str):
            inner = json.loads(result)
        elif isinstance(result, dict):
            inner = result
        else:
            return False
        return bool(inner.get("success", False))
    except (json.JSONDecodeError, ValueError, TypeError):
        return False


def _is_close_success(tool_response: str) -> bool:
    """Return True iff the close_kitchen response indicates success.

    ``close_kitchen`` returns the bare string ``\"Kitchen is closed.\"`` on
    success; on failure it returns a JSON envelope ``{\"success\": false}``.
    """
    text = (tool_response or "").strip()
    if text.startswith("{"):
        try:
            outer = json.loads(text)
            if isinstance(outer, dict):
                return bool(outer.get("success", False))
        except (json.JSONDecodeError, ValueError, TypeError):
            return False
        return False
    return "Kitchen is closed" in text or text == ""


def main() -> None:
    try:
        data = json.loads(sys.stdin.read())
    except (json.JSONDecodeError, ValueError, OSError):
        sys.exit(0)

    if not isinstance(data, dict):
        sys.exit(0)

    session_id = data.get("session_id", "")
    tool_name = data.get("tool_name", "")
    if not session_id or tool_name not in _HANDLED_TOOLS:
        sys.exit(0)

    if "/" in session_id or "\\" in session_id or session_id in {".", ".."}:
        sys.exit(0)

    tool_response = data.get("tool_response", "")

    if tool_name == "disable_quota_guard":
        if not _is_disable_success(tool_response):
            sys.exit(0)
        try:
            marker_path = quota_disable_marker_path(session_id)
            payload = json.dumps(
                {
                    "session_id": session_id,
                    "disabled_at": datetime.now(UTC).isoformat(),
                    "marker_version": 1,
                }
            )
            _atomic_write_marker(marker_path, payload)
        except (OSError, ValueError) as exc:
            sys.stderr.write(
                f"quota_guard_state_post_hook: marker write failed for {session_id}: {exc}\n"
            )
            sys.stdout.write(
                json.dumps(
                    {
                        "hookSpecificOutput": {
                            "hookEventName": "PostToolUse",
                            "updatedMCPToolOutput": _FAILURE_REWRITE,
                        }
                    }
                )
            )
        sys.exit(0)

    if tool_name == "close_kitchen":
        if not _is_close_success(tool_response):
            sys.exit(0)
        clear_quota_disable_marker(session_id)
        sys.exit(0)

    sys.exit(0)


if __name__ == "__main__":
    main()
