"""Inject a one-shot native Claude identity token into exploration tool input."""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path
from typing import Any

_HOOKS_DIR = str(Path(__file__).resolve().parent.parent)
if _HOOKS_DIR not in sys.path:
    sys.path.insert(0, _HOOKS_DIR)

from _exploration_request_record import (  # type: ignore[import-not-found]  # noqa: E402
    write_exploration_request_record,
)
from _hook_payload import resolve_state_root  # type: ignore[import-not-found]  # noqa: E402

EXPLORATION_REQUEST_IDENTITY_DENY_TRIGGER = "EXPLORATION REQUEST IDENTITY UNAVAILABLE"
_TOKEN_PARAM = "_autoskillit_exploration_request_token"
_TOOL_NAME = re.compile(
    r"mcp__.*autoskillit.*__(enable_exploration|submit_exploration_query|"
    r"get_exploration_page|resume_exploration_context)\Z"
)


def _deny(reason: str) -> None:
    print(
        json.dumps(
            {
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": "deny",
                    "permissionDecisionReason": (
                        f"{EXPLORATION_REQUEST_IDENTITY_DENY_TRIGGER}: {reason}"
                    ),
                }
            }
        )
    )


def _short_tool_name(raw_name: object) -> str | None:
    if not isinstance(raw_name, str):
        return None
    match = _TOOL_NAME.fullmatch(raw_name)
    return match.group(1) if match is not None else None


def main() -> None:
    try:
        data: Any = json.loads(sys.stdin.read())
    except Exception:
        return
    if not isinstance(data, dict):
        return
    if os.environ.get("AUTOSKILLIT_AGENT_BACKEND") == "codex":
        return
    if os.environ.get("AUTOSKILLIT_HEADLESS") == "1":
        return

    tool_name = _short_tool_name(data.get("tool_name"))
    if tool_name is None:
        return
    session_id = data.get("session_id")
    tool_input = data.get("tool_input")
    if (
        not isinstance(session_id, str)
        or not 0 < len(session_id) <= 128
        or not isinstance(tool_input, dict)
    ):
        _deny("supported tool event did not carry a bounded session_id and input mapping")
        return

    try:
        root = resolve_state_root(data.get("cwd") if isinstance(data.get("cwd"), str) else "")
        token = write_exploration_request_record(root, tool_name, session_id)
    except Exception as exc:
        sys.stderr.write(
            f"exploration_request_identity_guard: request record write failed: {exc}\n"
        )
        _deny("the correlated one-shot record could not be written")
        return

    updated_input = dict(tool_input)
    updated_input[_TOKEN_PARAM] = token
    print(
        json.dumps(
            {
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": "allow",
                    "updatedInput": updated_input,
                }
            }
        )
    )


if __name__ == "__main__":
    main()
