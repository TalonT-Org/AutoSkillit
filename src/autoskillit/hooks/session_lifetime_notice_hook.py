#!/usr/bin/env python3
"""Deliver one interactive-session lifetime warning from its notice file."""

from __future__ import annotations

import json
import logging
import os
import sys
from pathlib import Path

_HOOKS_DIR = str(Path(__file__).resolve().parent)
if _HOOKS_DIR not in sys.path:
    sys.path.insert(0, _HOOKS_DIR)
_RUNTIME_DIR = str(Path(_HOOKS_DIR) / "_runtime")
if _RUNTIME_DIR not in sys.path:
    sys.path.insert(0, _RUNTIME_DIR)

from _hook_settings import enforce_session_scope  # noqa: E402


def main() -> None:
    enforce_session_scope("interactive_only")

    try:
        event = json.load(sys.stdin)
        if not isinstance(event, dict):
            return
        event_name = event.get("hook_event_name")
        if event_name not in {"PostToolUse", "Stop"}:
            return

        notice_path = os.environ.get("AUTOSKILLIT_SESSION_LIFETIME_NOTICE")
        if not notice_path:
            return
        notice = json.loads(Path(notice_path).read_text(encoding="utf-8"))
        if not isinstance(notice, dict):
            return
        message = notice.get("message")
        if not isinstance(message, str) or not message:
            return

        output: dict[str, object] = {"systemMessage": message}
        if event_name == "PostToolUse":
            output["hookSpecificOutput"] = {
                "hookEventName": "PostToolUse",
                "additionalContext": message,
            }
        json.dump(output, sys.stdout)
        sys.stdout.flush()
        Path(notice_path).unlink()
    except Exception:
        logging.warning("session_lifetime_notice_failed", exc_info=True)
        return


if __name__ == "__main__":
    main()
