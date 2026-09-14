#!/usr/bin/env python3
"""Deny Codex automatic compaction before it can replace session history."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

_HOOKS_DIR = str(Path(__file__).resolve().parent.parent)
if _HOOKS_DIR not in sys.path:
    sys.path.insert(0, _HOOKS_DIR)
_RUNTIME_DIR = str(Path(_HOOKS_DIR) / "_runtime")
if _RUNTIME_DIR not in sys.path:
    sys.path.insert(0, _RUNTIME_DIR)

from _hook_constants import CODEX_AUTO_COMPACTION_DENIED_REASON  # noqa: E402

_SYSTEM_MESSAGE = (
    "AutoSkillit blocked automatic compaction before changing history. Start a new session, "
    "or compact manually and resume deliberately."
)


def main() -> None:
    """Emit Codex's synchronous veto only for its registered automatic trigger."""
    if os.environ.get("AUTOSKILLIT_AGENT_BACKEND") != "codex":
        return
    try:
        event = json.load(sys.stdin)
    except (json.JSONDecodeError, OSError, ValueError):
        return
    if not isinstance(event, dict):
        return
    if event.get("hook_event_name") != "PreCompact" or event.get("trigger") != "auto":
        return
    json.dump(
        {
            "continue": False,
            "stopReason": CODEX_AUTO_COMPACTION_DENIED_REASON,
            "systemMessage": _SYSTEM_MESSAGE,
        },
        sys.stdout,
    )


if __name__ == "__main__":
    main()
